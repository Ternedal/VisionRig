package dk.kaliv.visionrig.quest

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import androidx.core.content.ContextCompat
import dk.kaliv.visionrig.producer.EncodedCapture
import dk.kaliv.visionrig.producer.EncodedFrame
import dk.kaliv.visionrig.producer.ProducerProtocolException
import java.io.ByteArrayOutputStream
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

private const val HORIZON_HEADSET_CAMERA_PERMISSION =
    "horizonos.permission.HEADSET_CAMERA"

private const val KEY_CAMERA_POSITION =
    "com.meta.extra_metadata.position"
private const val KEY_CAMERA_SOURCE =
    "com.meta.extra_metadata.camera_source"

private val META_CAMERA_POSITION =
    CameraCharacteristics.Key(KEY_CAMERA_POSITION, Int::class.java)
private val META_CAMERA_SOURCE =
    CameraCharacteristics.Key(KEY_CAMERA_SOURCE, Int::class.java)

private const val CAMERA_SOURCE_PASSTHROUGH = 0

enum class QuestCameraPosition(val vendorValue: Int) {
    LEFT(0),
    RIGHT(1),
}

data class QuestCameraConfig(
    val width: Int = 1280,
    val height: Int = 960,
    val jpegQuality: Int = 85,
    val timeoutSeconds: Long = 10,
    val position: QuestCameraPosition = QuestCameraPosition.RIGHT,
) {
    init {
        require(width > 0 && width % 2 == 0)
        require(height > 0 && height % 2 == 0)
        require(jpegQuality in 1..100)
        require(timeoutSeconds in 1..60)
    }
}

class QuestPassthroughEncodedCapture(
    context: Context,
    private val config: QuestCameraConfig = QuestCameraConfig(),
) : EncodedCapture {
    private val appContext = context.applicationContext
    private val cameraManager =
        appContext.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val frameQueue = ArrayBlockingQueue<ByteArray>(1)

    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null
    private var camera: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null

    override val isOpen: Boolean
        get() = camera != null && session != null && reader != null

    @SuppressLint("MissingPermission")
    override fun open() {
        if (isOpen) return
        requirePermissions()

        val cameraId = selectPassthroughCamera()
        val characteristics = cameraManager.getCameraCharacteristics(cameraId)
        val map = characteristics.get(
            CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP
        ) ?: throw ProducerProtocolException(
            "Quest passthrough camera lacks stream configuration"
        )
        val supported = map.getOutputSizes(ImageFormat.YUV_420_888)
            ?.any { it.width == config.width && it.height == config.height }
            ?: false
        if (!supported) {
            throw ProducerProtocolException(
                "Quest passthrough camera does not support requested YUV size"
            )
        }

        val thread = HandlerThread("visionrig-quest-camera").apply { start() }
        val handler = Handler(thread.looper)
        cameraThread = thread
        cameraHandler = handler

        val imageReader = ImageReader.newInstance(
            config.width,
            config.height,
            ImageFormat.YUV_420_888,
            3,
        )
        reader = imageReader
        imageReader.setOnImageAvailableListener(
            { source ->
                val image = source.acquireLatestImage() ?: return@setOnImageAvailableListener
                try {
                    val jpeg = encodeJpeg(image)
                    frameQueue.poll()
                    frameQueue.offer(jpeg)
                } catch (_: Throwable) {
                    // capture() times out fail-closed if no valid frame can be encoded.
                } finally {
                    image.close()
                }
            },
            handler,
        )

        val opened = CountDownLatch(1)
        val openFailure = AtomicReference<Throwable?>()
        cameraManager.openCamera(
            cameraId,
            object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    camera = device
                    opened.countDown()
                }

                override fun onDisconnected(device: CameraDevice) {
                    openFailure.compareAndSet(
                        null,
                        ProducerProtocolException(
                            "Quest passthrough camera disconnected"
                        ),
                    )
                    device.close()
                    opened.countDown()
                }

                override fun onError(device: CameraDevice, error: Int) {
                    openFailure.compareAndSet(
                        null,
                        ProducerProtocolException(
                            "Quest passthrough camera error " + error
                        ),
                    )
                    device.close()
                    opened.countDown()
                }
            },
            handler,
        )

        try {
            await(opened, "Quest passthrough camera open")
        } catch (exc: RuntimeException) {
            close()
            throw exc
        }
        openFailure.get()?.let {
            close()
            throw ProducerProtocolException(
                "Quest passthrough camera failed to open",
                it,
            )
        }

        val device = camera
            ?: run {
                close()
                throw ProducerProtocolException(
                    "Quest passthrough camera open returned no device"
                )
            }

        val configured = CountDownLatch(1)
        val sessionFailure = AtomicReference<Throwable?>()
        val executor = Executor { command -> handler.post(command) }
        try {
            device.createCaptureSession(
                SessionConfiguration(
                    SessionConfiguration.SESSION_REGULAR,
                    listOf(OutputConfiguration(imageReader.surface)),
                    executor,
                    object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(
                            configuredSession: CameraCaptureSession,
                        ) {
                            session = configuredSession
                            configured.countDown()
                        }

                        override fun onConfigureFailed(
                            configuredSession: CameraCaptureSession,
                        ) {
                            sessionFailure.set(
                                ProducerProtocolException(
                                    "Quest passthrough capture session configuration failed"
                                )
                            )
                            configuredSession.close()
                            configured.countDown()
                        }
                    },
                ),
            )
        } catch (exc: Exception) {
            close()
            throw ProducerProtocolException(
                "Quest passthrough capture session unavailable",
                exc,
            )
        }

        try {
            await(configured, "Quest passthrough capture session")
        } catch (exc: RuntimeException) {
            close()
            throw exc
        }
        sessionFailure.get()?.let {
            close()
            throw ProducerProtocolException(
                "Quest passthrough capture session failed",
                it,
            )
        }

        val activeSession = session
            ?: run {
                close()
                throw ProducerProtocolException(
                    "Quest passthrough session returned no configured session"
                )
            }
        try {
            val request = device.createCaptureRequest(
                CameraDevice.TEMPLATE_PREVIEW
            ).apply {
                addTarget(imageReader.surface)
            }
            activeSession.setRepeatingRequest(
                request.build(),
                null,
                handler,
            )
        } catch (exc: Exception) {
            close()
            throw ProducerProtocolException(
                "Quest passthrough repeating capture failed",
                exc,
            )
        }
    }

    override fun capture(): EncodedFrame {
        if (!isOpen) {
            throw ProducerProtocolException(
                "Quest passthrough capture is not open"
            )
        }
        val jpeg = try {
            frameQueue.poll(config.timeoutSeconds, TimeUnit.SECONDS)
        } catch (exc: InterruptedException) {
            Thread.currentThread().interrupt()
            throw ProducerProtocolException(
                "Quest passthrough capture interrupted",
                exc,
            )
        } ?: throw ProducerProtocolException(
            "Quest passthrough capture timed out"
        )

        return EncodedFrame(
            bytes = jpeg,
            contentType = "image/jpeg",
        )
    }

    override fun close() {
        frameQueue.clear()

        try {
            session?.stopRepeating()
        } catch (_: Exception) {
        }
        try {
            session?.close()
        } catch (_: Exception) {
        }
        session = null

        try {
            camera?.close()
        } catch (_: Exception) {
        }
        camera = null

        try {
            reader?.close()
        } catch (_: Exception) {
        }
        reader = null

        cameraHandler = null
        cameraThread?.quitSafely()
        cameraThread = null
    }

    private fun requirePermissions() {
        val androidCamera = ContextCompat.checkSelfPermission(
            appContext,
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
        val headsetCamera = ContextCompat.checkSelfPermission(
            appContext,
            HORIZON_HEADSET_CAMERA_PERMISSION,
        ) == PackageManager.PERMISSION_GRANTED
        if (!androidCamera || !headsetCamera) {
            throw ProducerProtocolException(
                "Quest passthrough requires CAMERA and HEADSET_CAMERA permissions"
            )
        }
    }

    private fun selectPassthroughCamera(): String {
        for (cameraId in cameraManager.cameraIdList) {
            val characteristics =
                cameraManager.getCameraCharacteristics(cameraId)
            val source = characteristics.get(META_CAMERA_SOURCE)
            val position = characteristics.get(META_CAMERA_POSITION)
            if (
                source == CAMERA_SOURCE_PASSTHROUGH &&
                position == config.position.vendorValue
            ) {
                return cameraId
            }
        }
        throw ProducerProtocolException(
            "No matching Meta passthrough camera found"
        )
    }

    private fun await(latch: CountDownLatch, operation: String) {
        val completed = try {
            latch.await(config.timeoutSeconds, TimeUnit.SECONDS)
        } catch (exc: InterruptedException) {
            Thread.currentThread().interrupt()
            throw ProducerProtocolException(
                operation + " interrupted",
                exc,
            )
        }
        if (!completed) {
            throw ProducerProtocolException(operation + " timed out")
        }
    }

    private fun encodeJpeg(image: Image): ByteArray {
        val nv21 = yuv420ToNv21(image)
        val output = ByteArrayOutputStream()
        val compressed = YuvImage(
            nv21,
            ImageFormat.NV21,
            image.width,
            image.height,
            null,
        ).compressToJpeg(
            Rect(0, 0, image.width, image.height),
            config.jpegQuality,
            output,
        )
        if (!compressed) {
            throw ProducerProtocolException(
                "Unable to encode Quest passthrough frame as JPEG"
            )
        }
        return output.toByteArray()
    }
}

internal fun yuv420ToNv21(image: Image): ByteArray {
    require(image.format == ImageFormat.YUV_420_888)
    val width = image.width
    val height = image.height
    val output = ByteArray(width * height * 3 / 2)
    val planes = image.planes
    require(planes.size >= 3)

    copyPlane(
        plane = planes[0],
        width = width,
        height = height,
        destination = output,
        destinationOffset = 0,
    )

    val chromaOffset = width * height
    copyChromaNv21(
        uPlane = planes[1],
        vPlane = planes[2],
        width = width / 2,
        height = height / 2,
        destination = output,
        destinationOffset = chromaOffset,
    )
    return output
}

private fun copyPlane(
    plane: Image.Plane,
    width: Int,
    height: Int,
    destination: ByteArray,
    destinationOffset: Int,
) {
    val buffer = plane.buffer.duplicate()
    val base = buffer.position()
    val rowStride = plane.rowStride
    val pixelStride = plane.pixelStride
    var out = destinationOffset

    for (row in 0 until height) {
        for (col in 0 until width) {
            val index = base + row * rowStride + col * pixelStride
            destination[out++] = buffer.get(index)
        }
    }
}

private fun copyChromaNv21(
    uPlane: Image.Plane,
    vPlane: Image.Plane,
    width: Int,
    height: Int,
    destination: ByteArray,
    destinationOffset: Int,
) {
    val u = uPlane.buffer.duplicate()
    val v = vPlane.buffer.duplicate()
    val uBase = u.position()
    val vBase = v.position()
    var out = destinationOffset

    for (row in 0 until height) {
        for (col in 0 until width) {
            val uIndex = uBase + row * uPlane.rowStride + col * uPlane.pixelStride
            val vIndex = vBase + row * vPlane.rowStride + col * vPlane.pixelStride
            destination[out++] = v.get(vIndex)
            destination[out++] = u.get(uIndex)
        }
    }
}

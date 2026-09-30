package dk.kaliv.visionrig.producer

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class ProducerControlLoopConcurrencyTest {
    @Test
    fun concurrentStepsAreSerializedAcrossCaptureAndDelivery() {
        val server = MockWebServer()
        enqueueEnabledStep(
            server,
            revision = 1,
            sequence = 0,
            includeCapabilities = true,
        )
        enqueueEnabledStep(
            server,
            revision = 2,
            sequence = 1,
            includeCapabilities = false,
        )
        server.start()

        val firstCaptureEntered = CountDownLatch(1)
        val releaseFirstCapture = CountDownLatch(1)
        val secondCallerStarted = CountDownLatch(1)
        val captureCalls = AtomicInteger(0)

        val capture = object : EncodedCapture {
            @Volatile
            private var open = false

            override val isOpen: Boolean
                get() = open

            override fun open() {
                open = true
            }

            override fun close() {
                open = false
            }

            override fun capture(): EncodedFrame {
                val call = captureCalls.incrementAndGet()
                if (call == 1) {
                    firstCaptureEntered.countDown()
                    check(releaseFirstCapture.await(5, TimeUnit.SECONDS))
                }
                return EncodedFrame(byteArrayOf(call.toByte()))
            }
        }

        val dir = createTempDirectory("visionrig-step-lock").toFile()
        val gateway = VisionRigGatewayClient(
            gatewayUrl = server.url("/").toString(),
            token = "x".repeat(32),
            sourceId = "kaliv-android",
            sourceType = "camera",
            device = "test-camera",
            stateStore = FileProducerStateStore(File(dir, "state.json")),
        )
        val loop = ProducerControlLoop(gateway, capture)
        val pool = Executors.newFixedThreadPool(2)

        try {
            val first = pool.submit<ControlStepResult> {
                loop.step()
            }
            assertTrue(firstCaptureEntered.await(5, TimeUnit.SECONDS))

            val second = pool.submit<ControlStepResult> {
                secondCallerStarted.countDown()
                loop.step()
            }
            assertTrue(secondCallerStarted.await(5, TimeUnit.SECONDS))

            // Caller two is active, but must be blocked on the producer-step lock.
            assertEquals(1, captureCalls.get())
            assertFalse(second.isDone)

            releaseFirstCapture.countDown()

            assertEquals(
                SendStatus.ACCEPTED,
                first.get(5, TimeUnit.SECONDS).frameResult?.status,
            )
            assertEquals(
                SendStatus.ACCEPTED,
                second.get(5, TimeUnit.SECONDS).frameResult?.status,
            )
            assertEquals(2, captureCalls.get())
        } finally {
            releaseFirstCapture.countDown()
            pool.shutdownNow()
            loop.close()
            server.shutdown()
        }
    }


    @Test
    fun closeWaitsForActiveStepBeforeClosingCapture() {
        val server = MockWebServer()
        enqueueEnabledStep(server, revision = 1, sequence = 0)
        server.start()

        val captureEntered = CountDownLatch(1)
        val releaseCapture = CountDownLatch(1)
        val closeStarted = CountDownLatch(1)

        val capture = object : EncodedCapture {
            @Volatile
            private var open = false

            override val isOpen: Boolean
                get() = open

            override fun open() {
                open = true
            }

            override fun close() {
                open = false
            }

            override fun capture(): EncodedFrame {
                captureEntered.countDown()
                check(releaseCapture.await(5, TimeUnit.SECONDS))
                return EncodedFrame(byteArrayOf(1))
            }
        }

        val dir = createTempDirectory("visionrig-close-lock").toFile()
        val gateway = VisionRigGatewayClient(
            gatewayUrl = server.url("/").toString(),
            token = "x".repeat(32),
            sourceId = "kaliv-android",
            sourceType = "camera",
            device = "test-camera",
            stateStore = FileProducerStateStore(File(dir, "state.json")),
        )
        val loop = ProducerControlLoop(gateway, capture)
        val pool = Executors.newFixedThreadPool(2)

        try {
            val step = pool.submit<ControlStepResult> {
                loop.step()
            }
            assertTrue(captureEntered.await(5, TimeUnit.SECONDS))

            val close = pool.submit {
                closeStarted.countDown()
                loop.close()
            }
            assertTrue(closeStarted.await(5, TimeUnit.SECONDS))

            assertFalse(close.isDone)
            assertTrue(capture.isOpen)

            releaseCapture.countDown()

            assertEquals(
                SendStatus.ACCEPTED,
                step.get(5, TimeUnit.SECONDS).frameResult?.status,
            )
            close.get(5, TimeUnit.SECONDS)
            assertFalse(capture.isOpen)
        } finally {
            releaseCapture.countDown()
            pool.shutdownNow()
            loop.close()
            server.shutdown()
        }
    }

    private fun enqueueEnabledStep(
        server: MockWebServer,
        revision: Long,
        sequence: Long,
        includeCapabilities: Boolean = true,
    ) {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-desired-state/v2",
                  "source_id":"kaliv-android",
                  "enabled":true,
                  "revision":$revision,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        if (includeCapabilities) {
            server.enqueue(
                MockResponse().setResponseCode(200).setBody(
                    """
                    {
                      "schema":"visionrig/producer-capabilities/v2",
                      "max_payload_bytes":1048576,
                      "gateway_max_payload_bytes":1048576,
                      "core_max_payload_bytes":1048576,
                      "sensor_packet_schemas":["visionrig/sensor-packet/v2"],
                      "sensor_packet_compressions":["none","zlib","auto"],
                      "packet_payload_warning_utilization":0.8,
                      "packet_payload_critical_utilization":0.95
                    }
                    """.trimIndent()
                )
            )
        }
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-heartbeat-receipt/v1",
                  "status":"accepted",
                  "source_id":"kaliv-android",
                  "seen_utc":"2026-09-29T16:00:00Z",
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-android",
                  "source_type":"camera",
                  "frame_sequence":$sequence,
                  "event_id":"evt-$sequence",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
    }
}

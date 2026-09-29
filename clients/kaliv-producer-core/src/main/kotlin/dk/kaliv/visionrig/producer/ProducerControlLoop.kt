package dk.kaliv.visionrig.producer

interface EncodedCapture {
    val isOpen: Boolean
    fun open()
    fun close()
    fun capture(): EncodedFrame
}

data class EncodedFrame(
    val bytes: ByteArray,
    val contentType: String = "image/jpeg",
)

data class ControlStepResult(
    val desiredEnabled: Boolean,
    val appliedRevision: Long,
    val captureActive: Boolean,
    val frameResult: SendResult? = null,
)

class ProducerControlLoop(
    private val gateway: VisionRigGatewayClient,
    private val capture: EncodedCapture,
    private val capabilities: List<String> = emptyList(),
) : AutoCloseable {
    fun step(): ControlStepResult {
        val desired = try {
            gateway.fetchDesiredState()
        } catch (exc: Exception) {
            closeCaptureQuietly()
            throw exc
        }

        if (!desired.enabled) {
            closeCaptureQuietly()
            gateway.sendHeartbeat(
                captureActive = false,
                appliedRevision = desired.revision,
                capabilities = capabilities,
            )
            return ControlStepResult(
                desiredEnabled = false,
                appliedRevision = desired.revision,
                captureActive = false,
            )
        }

        try {
            if (!capture.isOpen) capture.open()
            gateway.sendHeartbeat(
                captureActive = true,
                appliedRevision = desired.revision,
                capabilities = capabilities,
            )
            val frame = capture.capture()
            val sent = gateway.sendEncoded(frame.bytes, frame.contentType)
            return ControlStepResult(
                desiredEnabled = true,
                appliedRevision = desired.revision,
                captureActive = true,
                frameResult = sent,
            )
        } catch (exc: Exception) {
            closeCaptureQuietly()
            throw exc
        }
    }

    override fun close() {
        closeCaptureQuietly()
    }

    private fun closeCaptureQuietly() {
        try {
            capture.close()
        } catch (_: Exception) {
            // Cleanup must not replace the control/transport failure that
            // triggered shutdown. Capture implementations must clear their
            // local open state before any best-effort hardware unbind.
        }
    }
}

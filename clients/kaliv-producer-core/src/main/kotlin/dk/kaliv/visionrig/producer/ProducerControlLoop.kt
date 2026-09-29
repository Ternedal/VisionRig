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

interface ProducerLoop : AutoCloseable {
    fun step(): ControlStepResult
}

class ProducerControlLoop(
    private val gateway: VisionRigGatewayClient,
    private val capture: EncodedCapture,
    private val capabilities: List<String> = emptyList(),
) : ProducerLoop {
    override fun step(): ControlStepResult {
        val desired = try {
            gateway.fetchDesiredState()
        } catch (exc: RuntimeException) {
            capture.close()
            throw exc
        }

        if (!desired.enabled) {
            capture.close()
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
        } catch (exc: RuntimeException) {
            capture.close()
            throw exc
        }
    }

    override fun close() {
        capture.close()
    }
}

package dk.kaliv.visionrig.quest

import android.content.Context
import dk.kaliv.visionrig.producer.ControlStepResult
import dk.kaliv.visionrig.producer.FileProducerStateStore
import dk.kaliv.visionrig.producer.ProducerControlLoop
import dk.kaliv.visionrig.producer.ProducerStateIdentity
import dk.kaliv.visionrig.producer.VisionRigGatewayClient
import java.io.File

class KalivQuestProducerSession private constructor(
    private val controlLoop: ProducerControlLoop,
    private val capture: QuestPassthroughEncodedCapture,
) : AutoCloseable {
    fun step(): ControlStepResult = controlLoop.step()

    override fun close() {
        controlLoop.close()
    }

    companion object {
        fun create(
            context: Context,
            gatewayUrl: String,
            token: String,
            sourceId: String = "kaliv-quest",
            device: String = "quest-passthrough",
            stateFile: File = File(
                context.filesDir,
                "visionrig/" + ProducerStateIdentity(
                    gatewayUrl = gatewayUrl,
                    sourceId = sourceId,
                    sourceType = "vr",
                ).defaultStateFileName(),
            ),
            captureConfig: QuestCameraConfig = QuestCameraConfig(),
        ): KalivQuestProducerSession {
            val store = FileProducerStateStore(stateFile)
            val gateway = VisionRigGatewayClient(
                gatewayUrl = gatewayUrl,
                token = token,
                sourceId = sourceId,
                sourceType = "vr",
                device = device,
                stateStore = store,
            )
            val capture = QuestPassthroughEncodedCapture(
                context = context,
                config = captureConfig,
            )
            val loop = ProducerControlLoop(
                gateway = gateway,
                capture = capture,
                capabilities = listOf(
                    "rgb",
                    "passthrough",
                    "camera2",
                    "meta-headset-camera",
                ),
            )
            return KalivQuestProducerSession(loop, capture)
        }
    }
}

package dk.kaliv.visionrig.producer

import java.io.IOException
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.HttpUrl.Companion.toHttpUrl

class VisionRigGatewayClient(
    gatewayUrl: String,
    token: String,
    private val sourceId: String,
    private val sourceType: String,
    private val device: String? = null,
    private val stateStore: ProducerStateStore,
    private val client: OkHttpClient = OkHttpClient(),
    private val json: Json = Json {
        ignoreUnknownKeys = false
        encodeDefaults = true
        explicitNulls = true
    },
) {
    private val baseUrl: HttpUrl
    private val bearerToken: String

    init {
        require(token.length >= 32) { "producer token must be at least 32 characters" }
        require(sourceId.isNotBlank() && sourceId.length <= 128)
        require(sourceType in setOf("camera", "screen", "vr", "image"))
        require(device == null || device.length <= 256)
        val parsed = gatewayUrl.toHttpUrl()
        require(parsed.username.isEmpty() && parsed.password.isEmpty()) {
            "gateway URL must not contain credentials"
        }
        require(parsed.query == null && parsed.fragment == null) {
            "gateway URL must not contain query or fragment"
        }
        baseUrl = parsed
        bearerToken = token
        stateStore.recover()
    }

    fun fetchDesiredState(): DesiredState {
        val url = baseUrl.newBuilder()
            .addPathSegments("api/v1/sensors")
            .addPathSegment(sourceId)
            .addPathSegment("desired-state")
            .build()
        val response = execute(Request.Builder().url(url).get().authorized().build())
        response.use {
            requireSuccess(it.code, "desired-state")
            val body = it.body?.string()
                ?: throw ProducerProtocolException("desired-state response has no body")
            val state = decode<DesiredState>(body, "desired-state")
            try {
                state.validate(sourceId)
            } catch (exc: IllegalArgumentException) {
                throw ProducerProtocolException("invalid VisionRig desired state", exc)
            }
            return state
        }
    }

    fun sendHeartbeat(
        captureActive: Boolean,
        appliedRevision: Long,
        capabilities: List<String> = emptyList(),
    ): HeartbeatReceipt {
        require(appliedRevision >= 0)
        val payload = HeartbeatRequest(
            sourceId = sourceId,
            sourceType = sourceType,
            device = device,
            capabilities = capabilities,
            captureActive = captureActive,
            appliedRevision = appliedRevision,
        )
        val url = baseUrl.newBuilder()
            .addPathSegments("api/v1/sensors/heartbeat")
            .build()
        val request = Request.Builder()
            .url(url)
            .post(
                json.encodeToString(payload)
                    .toRequestBody("application/json".toMediaType())
            )
            .authorized()
            .build()
        val response = execute(request)
        response.use {
            requireSuccess(it.code, "heartbeat")
            val body = it.body?.string()
                ?: throw ProducerProtocolException("heartbeat response has no body")
            val receipt = decode<HeartbeatReceipt>(body, "heartbeat")
            try {
                receipt.validate(sourceId)
            } catch (exc: IllegalArgumentException) {
                throw ProducerProtocolException("invalid VisionRig heartbeat receipt", exc)
            }
            return receipt
        }
    }

    fun sendEncoded(
        payload: ByteArray,
        contentType: String = "image/jpeg",
    ): SendResult {
        require(payload.isNotEmpty()) { "frame payload must not be empty" }
        require(contentType in setOf("image/jpeg", "image/png", "image/webp"))

        val reservation = stateStore.reserve()
        val urlBuilder = baseUrl.newBuilder()
            .addPathSegments("api/v1/frames/ingest")
            .addQueryParameter("source_id", sourceId)
            .addQueryParameter("source_type", sourceType)
            .addQueryParameter("frame_sequence", reservation.sequence.toString())
            .addQueryParameter("dropped_frames", reservation.pendingDropped.toString())
        device?.let { urlBuilder.addQueryParameter("device", it) }

        val request = Request.Builder()
            .url(urlBuilder.build())
            .post(payload.toRequestBody(contentType.toMediaType()))
            .authorized()
            .build()

        val response = try {
            execute(request)
        } catch (exc: IOException) {
            val state = stateStore.markDropped(reservation.sequence)
            return SendResult(
                SendStatus.DROPPED_UNAVAILABLE,
                reservation.sequence,
                state.pendingDropped,
            )
        }

        response.use {
            if (it.code == 429) {
                val state = stateStore.markDropped(reservation.sequence)
                return SendResult(
                    SendStatus.DROPPED_OVERLOAD,
                    reservation.sequence,
                    state.pendingDropped,
                )
            }
            if (it.code == 401 || it.code == 403) {
                stateStore.markDropped(reservation.sequence)
                throw ProducerAuthenticationException(
                    "VisionRig gateway rejected producer credentials"
                )
            }
            if (it.code != 200) {
                stateStore.markDropped(reservation.sequence)
                throw ProducerProtocolException(
                    "VisionRig frame endpoint returned HTTP " + it.code
                )
            }

            val body = it.body?.string()
                ?: run {
                    stateStore.markDropped(reservation.sequence)
                    throw ProducerProtocolException("frame response has no body")
                }
            val receipt = try {
                decode<FrameReceipt>(body, "frame")
            } catch (exc: RuntimeException) {
                stateStore.markDropped(reservation.sequence)
                throw exc
            }
            try {
                receipt.validate(
                    sourceId,
                    sourceType,
                    reservation.sequence,
                    reservation.pendingDropped,
                )
            } catch (exc: IllegalArgumentException) {
                stateStore.markDropped(reservation.sequence)
                throw ProducerProtocolException("VisionRig frame receipt mismatch", exc)
            }
            stateStore.markAccepted(reservation.sequence)
            return SendResult(
                SendStatus.ACCEPTED,
                reservation.sequence,
                0,
                receipt.eventId,
            )
        }
    }

    private inline fun <reified T> decode(body: String, label: String): T =
        try {
            json.decodeFromString<T>(body)
        } catch (exc: Exception) {
            throw ProducerProtocolException("invalid VisionRig " + label + " response", exc)
        }

    @Throws(IOException::class)
    private fun execute(request: Request) = client.newCall(request).execute()

    private fun requireSuccess(code: Int, label: String) {
        if (code == 401 || code == 403) {
            throw ProducerAuthenticationException(
                "VisionRig gateway rejected producer credentials"
            )
        }
        if (code != 200) {
            throw ProducerProtocolException(
                "VisionRig " + label + " endpoint returned HTTP " + code
            )
        }
    }

    private fun Request.Builder.authorized(): Request.Builder =
        header("Authorization", "Bearer " + bearerToken)
}

package dk.kaliv.visionrig.producer

import java.io.File
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest

class GatewayClientTest {

    private fun capabilitiesResponse(maxPayloadBytes: Long = 1024): MockResponse =
        MockResponse().setResponseCode(200).setBody(
            """
            {
              "schema":"visionrig/producer-capabilities/v2",
              "max_payload_bytes":$maxPayloadBytes,
              "gateway_max_payload_bytes":$maxPayloadBytes,
              "core_max_payload_bytes":$maxPayloadBytes,
              "sensor_packet_schemas":["visionrig/sensor-packet/v2"],
              "sensor_packet_compressions":["none","zlib","auto"],
              "packet_payload_warning_utilization":0.8,
              "packet_payload_critical_utilization":0.95
            }
            """.trimIndent()
        )

    private fun client(
        server: MockWebServer,
        sourceId: String = "kaliv-quest",
    ): VisionRigGatewayClient {
        val dir = createTempDirectory("visionrig-native-gateway").toFile()
        return VisionRigGatewayClient(
            gatewayUrl = server.url("/").toString(),
            token = "x".repeat(32),
            sourceId = sourceId,
            sourceType = "vr",
            device = "quest",
            stateStore = FileProducerStateStore(File(dir, "state.json")),
        )
    }

    @Test
    fun desiredStateAndHeartbeatAreStrictlyBoundToSource() {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                return when {
                    request.path == "/api/v1/sensors/kaliv-quest/desired-state" ->
                        MockResponse().setResponseCode(200).setBody(
                            """
                            {
                              "schema_id":"visionrig/sensor-desired-state/v2",
                              "source_id":"kaliv-quest",
                              "enabled":false,
                              "revision":7,
                              "production_authority":false
                            }
                            """.trimIndent()
                        )
                    request.path == "/api/v1/sensors/heartbeat" ->
                        MockResponse().setResponseCode(200).setBody(
                            """
                            {
                              "schema_id":"visionrig/sensor-heartbeat-receipt/v1",
                              "status":"accepted",
                              "source_id":"kaliv-quest",
                              "seen_utc":"2026-09-29T06:00:00Z",
                              "production_authority":false
                            }
                            """.trimIndent()
                        )
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        try {
            val gateway = client(server)
            val desired = gateway.fetchDesiredState()
            assertFalse(desired.enabled)
            assertEquals(7, desired.revision)

            val heartbeat = gateway.sendHeartbeat(
                captureActive = false,
                appliedRevision = desired.revision,
            )
            assertEquals("kaliv-quest", heartbeat.sourceId)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun acceptedFrameCarriesDurableSequenceAndDropCount() {
        val server = MockWebServer()
        server.enqueue(capabilitiesResponse())
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":0,
                  "event_id":"evt-native-0",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val gateway = client(server)
            val result = gateway.sendEncoded(byteArrayOf(1, 2, 3))
            assertEquals(SendStatus.ACCEPTED, result.status)
            assertEquals(0, result.frameSequence)
            assertEquals("evt-native-0", result.eventId)

            val capabilities = server.takeRequest()
            assertEquals("/api/v1/producer-capabilities", capabilities.path)
            val request = server.takeRequest()
            assertEquals("0", request.requestUrl?.queryParameter("frame_sequence"))
            assertEquals("0", request.requestUrl?.queryParameter("dropped_frames"))
            assertEquals("Bearer " + "x".repeat(32), request.getHeader("Authorization"))
            assertEquals("image/jpeg", request.getHeader("Content-Type"))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun overloadBecomesExplicitDropOnNextAcceptedFrame() {
        val server = MockWebServer()
        server.enqueue(capabilitiesResponse())
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":1,
                  "event_id":"evt-native-1",
                  "dropped_frames":1,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val gateway = client(server)
            val dropped = gateway.sendEncoded(byteArrayOf(1))
            assertEquals(SendStatus.DROPPED_OVERLOAD, dropped.status)
            assertEquals(1, dropped.pendingDroppedFrames)

            val accepted = gateway.sendEncoded(byteArrayOf(2))
            assertEquals(SendStatus.ACCEPTED, accepted.status)
            assertEquals(1, accepted.frameSequence)

            val capabilities = server.takeRequest()
            assertEquals("/api/v1/producer-capabilities", capabilities.path)
            server.takeRequest()
            val second = server.takeRequest()
            assertEquals("1", second.requestUrl?.queryParameter("dropped_frames"))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun disabledControlStepNeverOpensCapture() {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-desired-state/v2",
                  "source_id":"kaliv-quest",
                  "enabled":false,
                  "revision":3,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-heartbeat-receipt/v1",
                  "status":"accepted",
                  "source_id":"kaliv-quest",
                  "seen_utc":"2026-09-29T06:00:00Z",
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val capture = FakeCapture()
            val loop = ProducerControlLoop(client(server), capture)
            val step = loop.step()

            assertFalse(step.desiredEnabled)
            assertFalse(step.captureActive)
            assertFalse(capture.isOpen)
            assertEquals(0, capture.captureCalls)
            assertTrue(capture.closeCalls >= 1)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun controlStepClosesCaptureWhenDesiredStateTransportFails() {
        val server = MockWebServer()
        server.start()
        val gateway = client(server)
        server.shutdown()

        val capture = FakeCapture()
        capture.open()
        val loop = ProducerControlLoop(gateway, capture)

        kotlin.test.assertFailsWith<ProducerProtocolException> {
            loop.step()
        }

        assertFalse(capture.isOpen)
        assertTrue(capture.closeCalls >= 1)
    }

    @Test
    fun enabledControlStepOpensCaptureHeartbeatsAndSendsFrame() {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-desired-state/v2",
                  "source_id":"kaliv-quest",
                  "enabled":true,
                  "revision":9,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-heartbeat-receipt/v1",
                  "status":"accepted",
                  "source_id":"kaliv-quest",
                  "seen_utc":"2026-09-29T06:00:00Z",
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(capabilitiesResponse())
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":0,
                  "event_id":"evt-control-0",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val capture = FakeCapture()
            val loop = ProducerControlLoop(client(server), capture)
            val step = loop.step()

            assertTrue(step.desiredEnabled)
            assertTrue(step.captureActive)
            assertTrue(capture.isOpen)
            assertEquals(1, capture.captureCalls)
            assertEquals(SendStatus.ACCEPTED, step.frameResult?.status)

            val desired = server.takeRequest()
            val heartbeat = server.takeRequest()
            val capabilities = server.takeRequest()
            val frame = server.takeRequest()
            assertTrue(desired.path!!.contains("/desired-state"))
            assertEquals("/api/v1/sensors/heartbeat", heartbeat.path)
            assertEquals("/api/v1/producer-capabilities", capabilities.path)
            assertTrue(frame.path!!.startsWith("/api/v1/frames/ingest"))
        } finally {
            server.shutdown()
        }
    }



    @Test
    fun capabilityNegotiationRejectsOversizeFrameBeforeSequenceReservation() {
        val server = MockWebServer()
        server.enqueue(capabilitiesResponse(maxPayloadBytes = 1024))
        server.start()
        try {
            val dir = createTempDirectory("visionrig-native-cap").toFile()
            val stateFile = File(dir, "state.json")
            val gateway = VisionRigGatewayClient(
                gatewayUrl = server.url("/").toString(),
                token = "x".repeat(32),
                sourceId = "kaliv-quest",
                sourceType = "vr",
                device = "quest",
                stateStore = FileProducerStateStore(stateFile),
            )

            kotlin.test.assertFailsWith<ProducerProtocolException> {
                gateway.sendEncoded(ByteArray(1025) { 1 })
            }

            val state = FileProducerStateStore(stateFile).snapshot()
            assertEquals(0, state.nextSequence)
            assertEquals(0, state.pendingDropped)
            assertEquals(null, state.inflightSequence)

            val request = server.takeRequest()
            assertEquals("/api/v1/producer-capabilities", request.path)
            assertEquals(1, server.requestCount)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun capabilityNegotiationRejectsInconsistentPayloadLimit() {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema":"visionrig/producer-capabilities/v2",
                  "max_payload_bytes":2048,
                  "gateway_max_payload_bytes":1024,
                  "core_max_payload_bytes":4096,
                  "sensor_packet_schemas":["visionrig/sensor-packet/v2"],
                  "sensor_packet_compressions":["none"],
                  "packet_payload_warning_utilization":0.8,
                  "packet_payload_critical_utilization":0.95
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val gateway = client(server)
            kotlin.test.assertFailsWith<ProducerProtocolException> {
                gateway.fetchCapabilities()
            }
        } finally {
            server.shutdown()
        }
    }



    @Test
    fun heartbeatReportsNegotiatedPayloadTelemetryAfterFirstFrame() {
        val server = MockWebServer()
        server.enqueue(capabilitiesResponse(maxPayloadBytes = 2048))
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":0,
                  "event_id":"evt-cap-0",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-heartbeat-receipt/v1",
                  "status":"accepted",
                  "source_id":"kaliv-quest",
                  "seen_utc":"2026-09-29T08:00:00Z",
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            val dir = createTempDirectory("visionrig-native-heartbeat-cap").toFile()
            val gateway = VisionRigGatewayClient(
                gatewayUrl = server.url("/").toString(),
                token = "x".repeat(32),
                sourceId = "kaliv-quest",
                sourceType = "vr",
                device = "quest",
                stateStore = FileProducerStateStore(File(dir, "state.json")),
                capabilityRefreshSeconds = 30.0,
                monotonicMillis = { 1_000L },
                utcNow = { "2026-09-29T08:00:00Z" },
            )

            gateway.sendEncoded(byteArrayOf(1, 2, 3))
            gateway.sendHeartbeat(
                captureActive = true,
                appliedRevision = 5,
            )

            server.takeRequest()
            server.takeRequest()
            val heartbeat = server.takeRequest()
            val body = heartbeat.body.readUtf8()
            assertTrue(body.contains("\"negotiated_max_payload_bytes\":2048"))
            assertTrue(body.contains("\"capability_refreshed_utc\":\"2026-09-29T08:00:00Z\""))
            assertTrue(body.contains("\"capability_refresh_seconds\":30.0"))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun expiredCapabilityCacheRefreshesBeforeNextFrame() {
        val server = MockWebServer()
        server.enqueue(capabilitiesResponse(maxPayloadBytes = 2048))
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":0,
                  "event_id":"evt-cap-0",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.enqueue(capabilitiesResponse(maxPayloadBytes = 4096))
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-frame-receipt/v1",
                  "status":"processed",
                  "source_id":"kaliv-quest",
                  "source_type":"vr",
                  "frame_sequence":1,
                  "event_id":"evt-cap-1",
                  "dropped_frames":0,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            var now = 0L
            val dir = createTempDirectory("visionrig-native-cap-refresh").toFile()
            val gateway = VisionRigGatewayClient(
                gatewayUrl = server.url("/").toString(),
                token = "x".repeat(32),
                sourceId = "kaliv-quest",
                sourceType = "vr",
                device = "quest",
                stateStore = FileProducerStateStore(File(dir, "state.json")),
                capabilityRefreshSeconds = 1.0,
                monotonicMillis = { now },
                utcNow = { "2026-09-29T08:00:00Z" },
            )

            gateway.sendEncoded(byteArrayOf(1))
            now = 1_001L
            gateway.sendEncoded(byteArrayOf(2))

            val firstCapabilities = server.takeRequest()
            val firstFrame = server.takeRequest()
            val secondCapabilities = server.takeRequest()
            val secondFrame = server.takeRequest()
            assertEquals("/api/v1/producer-capabilities", firstCapabilities.path)
            assertTrue(firstFrame.path!!.startsWith("/api/v1/frames/ingest"))
            assertEquals("/api/v1/producer-capabilities", secondCapabilities.path)
            assertTrue(secondFrame.path!!.startsWith("/api/v1/frames/ingest"))
        } finally {
            server.shutdown()
        }
    }

    private class FakeCapture : EncodedCapture {
        override var isOpen: Boolean = false
            private set
        var captureCalls = 0
        var closeCalls = 0

        override fun open() {
            isOpen = true
        }

        override fun close() {
            closeCalls += 1
            isOpen = false
        }

        override fun capture(): EncodedFrame {
            captureCalls += 1
            return EncodedFrame(byteArrayOf(1, 2, 3))
        }
    }
}

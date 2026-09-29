package dk.kaliv.visionrig.producer

import java.io.File
import java.io.IOException
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

            server.takeRequest()
            val heartbeatRequest = server.takeRequest()
            assertTrue(
                heartbeatRequest.body.readUtf8().contains(
                    "\"schema_id\":\"visionrig/sensor-heartbeat/v6\""
                )
            )
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun acceptedFrameCarriesDurableSequenceAndDropCount() {
        val server = MockWebServer()
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
            val frame = server.takeRequest()
            assertTrue(desired.path!!.contains("/desired-state"))
            assertEquals("/api/v1/sensors/heartbeat", heartbeat.path)
            assertTrue(frame.path!!.startsWith("/api/v1/frames/ingest"))
        } finally {
            server.shutdown()
        }
    }



    @Test
    fun cleanupFailureDoesNotMaskDesiredStateTransportFailure() {
        val server = MockWebServer()
        server.start()
        val gateway = client(server)
        server.shutdown()

        val capture = object : EncodedCapture {
            override val isOpen: Boolean = true

            override fun open() {}

            override fun close() {
                throw IllegalStateException("close boom")
            }

            override fun capture(): EncodedFrame =
                error("capture must not be reached")
        }
        val loop = ProducerControlLoop(gateway, capture)

        val failure = kotlin.test.assertFailsWith<ProducerProtocolException> {
            loop.step()
        }

        assertTrue(failure.message!!.contains("gateway unavailable"))
    }

    @Test
    fun explicitLoopCloseIsBestEffort() {
        val server = MockWebServer()
        server.start()
        try {
            val capture = object : EncodedCapture {
                override val isOpen: Boolean = true
                override fun open() {}
                override fun close() {
                    throw IllegalStateException("close boom")
                }
                override fun capture(): EncodedFrame =
                    error("capture must not be reached")
            }
            val loop = ProducerControlLoop(client(server), capture)
            loop.close()
        } finally {
            server.shutdown()
        }
    }



    @Test
    fun checkedCaptureExceptionStillClosesFailClosed() {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "schema_id":"visionrig/sensor-desired-state/v2",
                  "source_id":"kaliv-quest",
                  "enabled":true,
                  "revision":11,
                  "production_authority":false
                }
                """.trimIndent()
            )
        )
        server.start()
        try {
            var closed = false
            val capture = object : EncodedCapture {
                override val isOpen: Boolean = false

                override fun open() {
                    throw IOException("camera storage unavailable")
                }

                override fun close() {
                    closed = true
                }

                override fun capture(): EncodedFrame =
                    error("capture must not be reached")
            }
            val loop = ProducerControlLoop(client(server), capture)

            kotlin.test.assertFailsWith<IOException> {
                loop.step()
            }

            assertTrue(closed)
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

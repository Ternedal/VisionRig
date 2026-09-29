package dk.kaliv.visionrig.producer

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlinx.coroutines.test.runTest

class ProducerRunnerTest {
    @Test
    fun disabledSourceUsesControlPollCadenceAndCapturesNothing() = runTest {
        val loop = FakeLoop(
            mutableListOf(
                ControlStepResult(
                    desiredEnabled = false,
                    appliedRevision = 1,
                    captureActive = false,
                ),
                ControlStepResult(
                    desiredEnabled = false,
                    appliedRevision = 1,
                    captureActive = false,
                ),
            )
        )
        val delays = mutableListOf<Long>()
        var iterations = 0
        val runner = ProducerRunner(
            loop = loop,
            config = ProducerRunConfig(
                fps = 5.0,
                disabledPollMillis = 2_000,
            ),
            delayMillis = { delays += it },
        )

        val captured = runner.run {
            iterations += 1
            iterations <= 2
        }

        assertEquals(0, captured)
        assertEquals(listOf(2_000L, 2_000L), delays)
        assertTrue(loop.closed)
    }

    @Test
    fun enabledSourceUsesFrameCadenceAndCountsDroppedCapture() = runTest {
        val loop = FakeLoop(
            mutableListOf(
                ControlStepResult(
                    desiredEnabled = true,
                    appliedRevision = 2,
                    captureActive = true,
                    frameResult = SendResult(
                        status = SendStatus.DROPPED_OVERLOAD,
                        frameSequence = 4,
                        pendingDroppedFrames = 1,
                    ),
                ),
                ControlStepResult(
                    desiredEnabled = true,
                    appliedRevision = 2,
                    captureActive = true,
                    frameResult = SendResult(
                        status = SendStatus.ACCEPTED,
                        frameSequence = 5,
                        pendingDroppedFrames = 0,
                        eventId = "evt-5",
                    ),
                ),
            )
        )
        val delays = mutableListOf<Long>()
        val runner = ProducerRunner(
            loop = loop,
            config = ProducerRunConfig(
                fps = 5.0,
                disabledPollMillis = 2_000,
                maxFrames = 2,
            ),
            delayMillis = { delays += it },
        )

        val captured = runner.run()

        assertEquals(2, captured)
        assertEquals(listOf(200L), delays)
        assertTrue(loop.closed)
    }

    @Test
    fun runnerClosesLoopWhenStepFails() = runTest {
        val loop = object : ProducerLoop {
            var closed = false

            override fun step(): ControlStepResult {
                throw ProducerProtocolException("boom")
            }

            override fun close() {
                closed = true
            }
        }
        val runner = ProducerRunner(
            loop = loop,
            delayMillis = {},
        )

        kotlin.test.assertFailsWith<ProducerProtocolException> {
            runner.run()
        }

        assertTrue(loop.closed)
    }

    @Test
    fun rejectsUnsafeCadenceConfiguration() {
        kotlin.test.assertFailsWith<IllegalArgumentException> {
            ProducerRunConfig(fps = 0.0)
        }
        kotlin.test.assertFailsWith<IllegalArgumentException> {
            ProducerRunConfig(fps = 61.0)
        }
        kotlin.test.assertFailsWith<IllegalArgumentException> {
            ProducerRunConfig(disabledPollMillis = 100)
        }
        kotlin.test.assertFailsWith<IllegalArgumentException> {
            ProducerRunConfig(maxFrames = -1)
        }
    }



    @Test
    fun closeFailureDoesNotMaskPrimaryStepFailure() = runTest {
        val loop = object : ProducerLoop {
            override fun step(): ControlStepResult {
                throw ProducerProtocolException("step boom")
            }

            override fun close() {
                throw IllegalStateException("close boom")
            }
        }
        val runner = ProducerRunner(
            loop = loop,
            delayMillis = {},
        )

        val failure = kotlin.test.assertFailsWith<ProducerProtocolException> {
            runner.run()
        }

        assertEquals("step boom", failure.message)
        assertEquals(1, failure.suppressed.size)
        assertEquals("close boom", failure.suppressed.single().message)
    }

    @Test
    fun closeFailureSurfacesOnNormalCompletion() = runTest {
        val loop = object : ProducerLoop {
            override fun step(): ControlStepResult =
                error("step must not be reached")

            override fun close() {
                throw IllegalStateException("close boom")
            }
        }
        val runner = ProducerRunner(
            loop = loop,
            delayMillis = {},
        )

        val failure = kotlin.test.assertFailsWith<IllegalStateException> {
            runner.run(shouldContinue = { false })
        }

        assertEquals("close boom", failure.message)
    }

    private class FakeLoop(
        private val steps: MutableList<ControlStepResult>,
    ) : ProducerLoop {
        var closed = false

        override fun step(): ControlStepResult =
            steps.removeFirst()

        override fun close() {
            closed = true
        }
    }
}

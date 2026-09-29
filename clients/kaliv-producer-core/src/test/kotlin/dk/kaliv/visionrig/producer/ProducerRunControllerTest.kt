package dk.kaliv.visionrig.producer

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest

@OptIn(ExperimentalCoroutinesApi::class)
class ProducerRunControllerTest {
    @Test
    fun startIsIdempotentAndCompletionResetsRunningState() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val scope = TestScope(dispatcher)
        val gate = CompletableDeferred<Unit>()

        val controller = ProducerRunController(
            scope = scope,
            runnerFactory = {
                ProducerRunner(
                    loop = object : ProducerLoop {
                        override fun step(): ControlStepResult {
                            error("step should not be reached")
                        }

                        override fun close() {}
                    },
                    config = ProducerRunConfig(maxFrames = 0),
                    delayMillis = {},
                )
            },
        )

        val started = controller.start(
            shouldContinue = {
                gate.complete(Unit)
                false
            }
        )
        val duplicate = controller.start()

        assertTrue(started)
        assertFalse(duplicate)

        advanceUntilIdle()
        assertTrue(gate.isCompleted)
        assertFalse(controller.isRunning)

        controller.close()
    }

    @Test
    fun stopCancelsActiveRunnerAndAllowsRestart() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val scope = TestScope(dispatcher)
        val loop = BlockingLoop()

        val controller = ProducerRunController(
            scope = scope,
            runnerFactory = {
                ProducerRunner(
                    loop = loop,
                    config = ProducerRunConfig(fps = 5.0),
                    delayMillis = { kotlinx.coroutines.delay(it) },
                )
            },
        )

        assertTrue(controller.start())
        testScheduler.runCurrent()
        assertTrue(controller.isRunning)

        controller.stop()
        advanceUntilIdle()

        assertFalse(controller.isRunning)
        assertTrue(loop.closed)

        assertTrue(controller.start(shouldContinue = { false }))
        advanceUntilIdle()
        assertFalse(controller.isRunning)
    }



    @Test
    fun cancelledOldJobCannotClearRestartedJobReference() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val scope = TestScope(dispatcher)
        val firstLoop = BlockingLoop()
        val secondLoop = BlockingLoop()
        var factoryCalls = 0

        val controller = ProducerRunController(
            scope = scope,
            runnerFactory = {
                factoryCalls += 1
                ProducerRunner(
                    loop = if (factoryCalls == 1) firstLoop else secondLoop,
                    config = ProducerRunConfig(fps = 5.0),
                    delayMillis = { kotlinx.coroutines.delay(it) },
                )
            },
        )

        assertTrue(controller.start())
        testScheduler.runCurrent()
        controller.stop()

        assertTrue(controller.start())
        testScheduler.runCurrent()
        assertTrue(controller.isRunning)

        controller.stop()
        advanceUntilIdle()
        assertFalse(controller.isRunning)
        assertTrue(firstLoop.closed)
        assertTrue(secondLoop.closed)
    }

    @Test
    fun normalStopDoesNotReportFailure() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val scope = TestScope(dispatcher)
        val loop = BlockingLoop()
        var failures = 0

        val controller = ProducerRunController(
            scope = scope,
            runnerFactory = {
                ProducerRunner(
                    loop = loop,
                    config = ProducerRunConfig(fps = 5.0),
                    delayMillis = { kotlinx.coroutines.delay(it) },
                )
            },
        )

        assertTrue(
            controller.start(
                onFailure = { failures += 1 },
            )
        )
        testScheduler.runCurrent()
        controller.stop()
        advanceUntilIdle()

        assertEquals(0, failures)
        assertFalse(controller.isRunning)
        assertTrue(loop.closed)
    }

    private class BlockingLoop : ProducerLoop {
        var closed = false

        override fun step(): ControlStepResult =
            ControlStepResult(
                desiredEnabled = false,
                appliedRevision = 0,
                captureActive = false,
            )

        override fun close() {
            closed = true
        }
    }
}

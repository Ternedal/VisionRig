package dk.kaliv.visionrig.producer

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class ProducerRunController(
    private val scope: CoroutineScope,
    private val runnerFactory: () -> ProducerRunner,
) : AutoCloseable {
    private val started = AtomicBoolean(false)
    private val jobRef = AtomicReference<Job?>(null)

    val isRunning: Boolean
        get() = jobRef.get()?.isActive == true

    fun start(
        shouldContinue: () -> Boolean = { true },
        onCompleted: (Int) -> Unit = {},
        onFailure: (Throwable) -> Unit = {},
    ): Boolean {
        if (!started.compareAndSet(false, true)) {
            return false
        }

        val job = scope.launch {
            try {
                val captured = runnerFactory().run(shouldContinue)
                onCompleted(captured)
            } catch (exc: Throwable) {
                onFailure(exc)
                throw exc
            } finally {
                jobRef.set(null)
                started.set(false)
            }
        }
        jobRef.set(job)
        return true
    }

    fun stop() {
        jobRef.getAndSet(null)?.cancel()
        started.set(false)
    }

    override fun close() {
        stop()
    }
}

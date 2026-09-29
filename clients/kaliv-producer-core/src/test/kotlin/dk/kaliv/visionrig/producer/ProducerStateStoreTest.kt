package dk.kaliv.visionrig.producer

import java.io.File
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertFailsWith

class ProducerStateStoreTest {
    @Test
    fun reserveAcceptAndDropFollowReferenceSemantics() {
        val dir = createTempDirectory("visionrig-native-state").toFile()
        val store = FileProducerStateStore(File(dir, "state.json"))

        val first = store.reserve()
        assertEquals(0, first.sequence)
        assertEquals(0, first.pendingDropped)

        val dropped = store.markDropped(first.sequence)
        assertEquals(1, dropped.nextSequence)
        assertEquals(1, dropped.pendingDropped)
        assertNull(dropped.inflightSequence)

        val second = store.reserve()
        assertEquals(1, second.sequence)
        assertEquals(1, second.pendingDropped)

        val accepted = store.markAccepted(second.sequence)
        assertEquals(2, accepted.nextSequence)
        assertEquals(0, accepted.pendingDropped)
        assertNull(accepted.inflightSequence)
    }

    @Test
    fun restartConvertsInflightFrameIntoExplicitDrop() {
        val dir = createTempDirectory("visionrig-native-recover").toFile()
        val file = File(dir, "state.json")
        val firstProcess = FileProducerStateStore(file)
        val reserved = firstProcess.reserve()
        assertEquals(0, reserved.sequence)

        val secondProcess = FileProducerStateStore(file)
        val recovered = secondProcess.recover()
        assertEquals(1, recovered.nextSequence)
        assertEquals(1, recovered.pendingDropped)
        assertNull(recovered.inflightSequence)

        val next = secondProcess.reserve()
        assertEquals(1, next.sequence)
        assertEquals(1, next.pendingDropped)
    }

    @Test
    fun cannotResolveWrongInflightSequence() {
        val dir = createTempDirectory("visionrig-native-mismatch").toFile()
        val store = FileProducerStateStore(File(dir, "state.json"))
        store.reserve()

        assertFailsWith<IllegalStateException> {
            store.markAccepted(99)
        }
    }
}

package dk.kaliv.visionrig.quest

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class QuestCameraConfigTest {
    @Test
    fun defaultsMatchMetaPassthroughBaseline() {
        val config = QuestCameraConfig()
        assertEquals(1280, config.width)
        assertEquals(960, config.height)
        assertEquals(85, config.jpegQuality)
        assertEquals(QuestCameraPosition.RIGHT, config.position)
    }

    @Test
    fun rejectsInvalidBounds() {
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 0)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(height = 0)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 1279)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(height = 959)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(jpegQuality = 101)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(timeoutSeconds = 0)
        }
    }
}

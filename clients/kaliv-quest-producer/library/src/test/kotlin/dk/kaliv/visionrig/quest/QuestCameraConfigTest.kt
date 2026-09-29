package dk.kaliv.visionrig.quest

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class QuestCameraConfigTest {
    @Test
    fun defaultsMatchMetaPassthroughBaseline() {
        val config = QuestCameraConfig()
        assertEquals(null, config.width)
        assertEquals(null, config.height)
        assertEquals(85, config.jpegQuality)
        assertEquals(QuestCameraPosition.RIGHT, config.position)
    }

    @Test
    fun rejectsInvalidBounds() {
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 1280)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(height = 960)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 0, height = 960)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 1280, height = 0)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 1279, height = 960)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(width = 1280, height = 959)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(jpegQuality = 101)
        }
        assertFailsWith<IllegalArgumentException> {
            QuestCameraConfig(timeoutSeconds = 0)
        }
    }
}

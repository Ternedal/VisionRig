package dk.kaliv.visionrig.android

import androidx.camera.core.CameraSelector
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class CameraXCaptureConfigTest {
    @Test
    fun defaultsAreBoundedForKalivCapture() {
        val config = CameraXCaptureConfig()
        assertEquals(85, config.jpegQuality)
        assertEquals(10, config.timeoutSeconds)
        assertEquals(CameraSelector.LENS_FACING_BACK, config.lensFacing)
    }

    @Test
    fun rejectsInvalidQualityTimeoutAndLens() {
        assertFailsWith<IllegalArgumentException> {
            CameraXCaptureConfig(jpegQuality = 0)
        }
        assertFailsWith<IllegalArgumentException> {
            CameraXCaptureConfig(timeoutSeconds = 0)
        }
        assertFailsWith<IllegalArgumentException> {
            CameraXCaptureConfig(lensFacing = 99)
        }
    }
}

package dk.kaliv.visionrig.producer

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

class ProducerStateIdentityTest {
    @Test
    fun stateIdentityIsStableAndFileSafe() {
        val identity = ProducerStateIdentity(
            gatewayUrl = "http://visionrig.local:8111/",
            sourceId = "kaliv-android",
            sourceType = "camera",
        )

        val first = identity.key()
        val second = identity.key()

        assertEquals(first, second)
        assertTrue(first.matches(Regex("^producer-[0-9a-f]{64}$")))
        assertEquals(first + ".json", identity.defaultStateFileName())
    }

    @Test
    fun gatewayAndSourceTypeArePartOfDurableStateIdentity() {
        val base = ProducerStateIdentity(
            gatewayUrl = "http://visionrig-a.local:8111",
            sourceId = "kaliv",
            sourceType = "camera",
        )
        val otherGateway = ProducerStateIdentity(
            gatewayUrl = "http://visionrig-b.local:8111",
            sourceId = "kaliv",
            sourceType = "camera",
        )
        val otherType = ProducerStateIdentity(
            gatewayUrl = "http://visionrig-a.local:8111",
            sourceId = "kaliv",
            sourceType = "vr",
        )

        assertNotEquals(base.key(), otherGateway.key())
        assertNotEquals(base.key(), otherType.key())
    }

    @Test
    fun trailingGatewaySlashDoesNotForkStateIdentity() {
        val withoutSlash = ProducerStateIdentity(
            gatewayUrl = "http://visionrig.local:8111",
            sourceId = "kaliv",
            sourceType = "camera",
        )
        val withSlash = ProducerStateIdentity(
            gatewayUrl = "http://visionrig.local:8111/",
            sourceId = "kaliv",
            sourceType = "camera",
        )

        assertEquals(withoutSlash.key(), withSlash.key())
    }
}

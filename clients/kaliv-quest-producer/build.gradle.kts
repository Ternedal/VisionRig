plugins {
    id("com.android.library") version "9.5.1" apply false
    // AGP 9 compiles Kotlin with its built-in support, using the Kotlin Gradle
    // plugin found on the build classpath. Pin it here so this build reads the
    // Kotlin metadata of kaliv-producer-core (same version, see its build file).
    // Bump all three producer builds together.
    id("org.jetbrains.kotlin.android") version "2.4.21" apply false
}

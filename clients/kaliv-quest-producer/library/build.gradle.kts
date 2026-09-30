plugins {
    id("com.android.library")
}

android {
    namespace = "dk.kaliv.visionrig.quest"
    compileSdk = 36

    defaultConfig {
        minSdk = 34
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        consumerProguardFiles("consumer-rules.pro")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    api("dk.kaliv.visionrig:kaliv-producer-core:0.1.0")

    implementation("androidx.core:core-ktx:1.17.0")

    testImplementation("org.jetbrains.kotlin:kotlin-test-junit5:2.2.0")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.11.4")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine:5.11.4")
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
}

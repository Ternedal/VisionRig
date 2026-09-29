pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

includeBuild("../kaliv-producer-core") {
    dependencySubstitution {
        substitute(module("dk.kaliv.visionrig:kaliv-producer-core"))
            .using(project(":"))
    }
}

rootProject.name = "kaliv-quest-producer"
include(":library")

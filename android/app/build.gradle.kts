import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")      // Liner Notes' engine (the same Python code as the Windows app) runs inside the app
}

// The app has its own number in ANDROID_VERSION (1.0.3 -> versionName 1.0.3, versionCode 1000003).
// The engine inside it is the PC app's code, so it keeps the PC's number from VERSION.
val appVersion = rootProject.file("../ANDROID_VERSION").takeIf { it.exists() }?.readText()?.trim() ?: "0.0.1"
val engineVersion = rootProject.file("../VERSION").takeIf { it.exists() }?.readText()?.trim() ?: "dev"
val parts = appVersion.split(".").map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0)
val appVersionCode = parts[0] * 1_000_000 + parts[1] * 1_000 + parts[2]

android {
    namespace = "app.linernotes"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.linernotes"
        minSdk = 24
        targetSdk = 36
        versionCode = appVersionCode
        versionName = appVersion
        buildConfigField("String", "ENGINE_VERSION", "\"$engineVersion\"")
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    // display.html and the fonts, which the engine serves; copied out of the app at first start
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("engineAssets"))

    buildFeatures { buildConfig = true }

    // direct: the APK on GitHub, which updates itself. play: the Google Play copy, which Play updates.
    flavorDimensions += "store"
    productFlavors {
        create("direct") { dimension = "store"; buildConfigField("boolean", "SELF_UPDATE", "true") }
        create("play") { dimension = "store"; buildConfigField("boolean", "SELF_UPDATE", "false") }
    }

    signingConfigs {
        create("release") {
            val ks = System.getenv("ANDROID_KEYSTORE_FILE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: "upload"
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD") ?: System.getenv("ANDROID_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val rel = signingConfigs.getByName("release")
            if (rel.storeFile != null) signingConfig = rel
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

val engineAssets = tasks.register<Copy>("engineAssets") {
    from(rootProject.file("../app")) { include("display.html", "fonts/**") }
    into(layout.buildDirectory.dir("engineAssets/engine"))
}
tasks.named("preBuild") { dependsOn(engineAssets) }

// Pure-Python wheels of aiohttp and its helpers, built by the workflow (their compiled versions aren't made for Android)
val wheelDir = rootProject.file("wheels")

chaquopy {
    defaultConfig {
        version = "3.11"   // 3.12+ has no 32-bit build, and many old phones are 32-bit
        System.getenv("CHAQUOPY_BUILD_PYTHON")?.let { buildPython(it) }
        extractPackages("certifi")      // its certificate file has to be a real file for SSL
        pip {
            if (wheelDir.isDirectory) {
                options("--find-links", wheelDir.absolutePath)
                wheelDir.listFiles { f -> f.name.endsWith(".whl") }?.sortedBy { it.name }?.forEach { install(it.absolutePath) }
            } else {
                install("aiohttp")
            }
            install("numpy")
            install("pillow")
        }
    }
    sourceSets {
        getByName("main") { srcDir(rootProject.file("../app")) }
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    "playImplementation"("com.android.billingclient:billing:8.0.0")    // Liner Notes Plus, sold through Google Play
}

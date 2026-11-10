import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// release 签名（11-11 落地）：keystore.properties（gitignored）持有口令与
// 路径；文件缺失=回退 debug 签名并保持可装（模板见 keystore.properties.example）。
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val hasReleaseKeystore = keystoreProps.getProperty("storeFile") != null

android {
    namespace = "com.cubemanager.turnpage"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.cubemanager.turnpage"
        minSdk = 26
        targetSdk = 35
        versionCode = 17
        versionName = "0.16.2"
    }

    signingConfigs {
        if (hasReleaseKeystore) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKeystore)
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    // 扫码连接首选 Google Code Scanner（标准扫码 UI + 免相机权限）；
    // 无 GMS 设备回退 zxing 内嵌扫描页（含相机权限申请）；appcompat 为其
    // ScanActivity 运行时依赖（宿主主题保持非 appcompat 不受影响）
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    testImplementation("junit:junit:4.13.2")
}

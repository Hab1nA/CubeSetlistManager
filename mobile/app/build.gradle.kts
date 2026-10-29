plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.cubemanager.turnpage"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.cubemanager.turnpage"
        minSdk = 26
        targetSdk = 35
        versionCode = 14
        versionName = "0.13.2"
    }

    buildTypes {
        release {
            // ponytail: 第一版 debug 签名直装平板；分发稳定后换 release keystore
            isMinifyEnabled = false
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

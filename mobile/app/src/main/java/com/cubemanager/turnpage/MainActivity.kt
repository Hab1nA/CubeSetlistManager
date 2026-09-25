package com.cubemanager.turnpage

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/** WebView 壳：加载电脑端 APP 版控制页（http://<ip>:8767/），页面代码零改动。
 *  首次连接的地址输入内嵌在欢迎页（不弹窗）；地址记忆在 SharedPreferences
 *  （旧端口自动迁移）。instance 供 TurnService 执行测试推送时的「切后台」。 */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var welcome: LinearLayout
    private lateinit var webView: WebView
    private lateinit var addrInput: EditText
    private var hadError = false
    private var loaded = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private var timeoutTask: Runnable? = null

    companion object {
        // 私网/环回 IPv4（与电脑端 valid_push_ip 同族规则）
        private val PRIVATE_HOST = Regex(
            "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2\\d|3[01])\\.|127\\.)[\\d.]+$")
        private const val DEFAULT_ADDR = "192.168.137.1:8767"

        @Volatile
        var instance: MainActivity? = null
            private set
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instance = this
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        status = TextView(this).apply {
            setPadding(dp(8), 0, dp(8), 0)
            gravity = Gravity.CENTER
            textSize = 14f
            visibility = ViewGroup.GONE
        }
        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            setBackgroundColor(0xFF14161A.toInt())
            addJavascriptInterface(Bridge(), "CubeApp")
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(view: WebView?, errorCode: Int,
                                             description: String?, failingUrl: String?) {
                    showStatus("加载失败（$errorCode）：$description", true)
                    welcome.visibility = ViewGroup.VISIBLE
                }
                // 只允许私网 IP：防止误输公网地址或被页面跳走
                override fun shouldOverrideUrlLoading(
                    view: WebView?, request: WebResourceRequest?): Boolean {
                    val host = request?.url?.host ?: return false
                    if (PRIVATE_HOST.matches(host)) return false
                    showStatus("已拦截非局域网地址：$host", true)
                    welcome.visibility = ViewGroup.VISIBLE
                    return true
                }
                override fun onPageFinished(view: WebView?, url: String?) {
                    if (hadError || url == null || url.startsWith("about:")) return
                    loaded = true
                    timeoutTask?.let { mainHandler.removeCallbacks(it) }
                    welcome.visibility = ViewGroup.GONE
                }
            }
        }
        val addrInput = EditText(this).apply {
            hint = DEFAULT_ADDR
            setText(prefs().getString("addr", null)?.let {
                it.removePrefix("http://")
            } ?: DEFAULT_ADDR)
            setSingleLine(true)
            setTextColor(0xFFe9ebef.toInt())
            setHintTextColor(0xFF565b64.toInt())
            background = GradientDrawable().apply {
                setColor(0xFF262a32.toInt())
                cornerRadius = dp(10).toFloat()
            }
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        this.addrInput = addrInput
        welcome = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(32), 0, dp(32), 0)
            setBackgroundColor(0xFF14161A.toInt())
            addView(TextView(this@MainActivity).apply {
                text = "Cube 翻谱"
                textSize = 34f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(0xFF7fe896.toInt())
            })
            addView(TextView(this@MainActivity).apply {
                text = "输入电脑端设置页的「APP 连接地址」"
                textSize = 15f
                setTextColor(0xFF9aa0aa.toInt())
                setPadding(0, dp(16), 0, dp(24))
            })
            addView(status, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { bottomMargin = dp(12) })
            addView(addrInput, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(bigButton("连 接"), LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(16) })
        }
        val root = FrameLayout(this).apply {
            // edge-to-edge：按系统栏+刘海实际尺寸让出边界（targetSdk 35 强制）
            setOnApplyWindowInsetsListener { v, insets ->
                val bar = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars()
                        or android.view.WindowInsets.Type.displayCutout())
                v.setPadding(bar.left, bar.top, bar.right, bar.bottom)
                insets
            }
            addView(webView, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT))
            addView(welcome, FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT))
        }
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        // 电池优化豁免：防 ROM 杀后台/解绑无障碍——标准弹窗确认一次永久生效
        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                startActivity(Intent(
                    android.provider.Settings
                        .ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    android.net.Uri.parse("package:$packageName")))
            } catch (e: Exception) {
            }
        }
        startForegroundService(Intent(this, TurnService::class.java))
        // 旧版默认端口 8765 现在是浏览器版页面：自动迁移到 APP 版 8767
        val saved = prefs().getString("addr", null)?.let {
            if (":8765" in it) it.replace(":8765", ":8767").also { up ->
                prefs().edit().putString("addr", up).apply()
            } else it
        }
        if (saved.isNullOrEmpty()) {
            status.visibility = ViewGroup.GONE
            addrInput.setText(DEFAULT_ADDR)
        } else {
            connect(saved)
        }
    }

    override fun onResume() {
        super.onResume()
    }

    private fun prefs() = getSharedPreferences("cube", MODE_PRIVATE)

    private fun dp(v: Int) = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun bigButton(text: String): Button =
        Button(this).apply {
            this.text = text
            textSize = 16f
            minHeight = dp(48)
            setTextColor(Color.BLACK)
            background = GradientDrawable().apply {
                setColor(0xFF7fe896.toInt())
                cornerRadius = dp(12).toFloat()
            }
            setPadding(dp(40), 0, dp(40), 0)
        }

    private fun showStatus(text: String, isError: Boolean) {
        status.text = text
        status.setTextColor(if (isError) 0xFFB00020.toInt() else 0xFF9aa0aa.toInt())
        status.visibility = ViewGroup.VISIBLE
    }

    /** 连接并带反馈：成功隐藏欢迎页；15s 未完成提示超时。 */
    private fun connect(url: String) {
        webView.stopLoading()
        loaded = false
        hadError = false
        showStatus("连接中 $url …", false)
        timeoutTask = Runnable {
            if (!loaded) showStatus("连接超时：请确认电脑已启动、热点已连", true)
        }
        mainHandler.postDelayed(timeoutTask!!, 15000)
        webView.loadUrl(url)
    }

    private fun doConnect() {
        val addr = addrInput.text.toString().trim()
        if (addr.isEmpty()) {
            showStatus("请输入电脑地址", true)
            return
        }
        val url = if ("://" in addr) addr else "http://$addr"
        prefs().edit().putString("addr", url).apply()
        connect(url)
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else moveTaskToBack(true)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        mainHandler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    /** JS 桥：设置面板的「连接设置」落到原生层（改地址需重启 APP）。
     *  方法在 JS 桥线程执行；弹确认框切主线程并阻塞等用户选择。 */
    private inner class Bridge {

        @android.webkit.JavascriptInterface
        fun requestAddressChange(url: String): String {
            val clean = url.trim()
            val host = android.net.Uri.parse(
                if ("://" in clean) clean else "http://$clean").host
            if (host == null || !PRIVATE_HOST.matches(host)) return "invalid"
            val prefs = prefs()
            val old = prefs.getString("addr", "") ?: ""
            if (clean == old) return "same"
            prefs.edit().putString("addr", clean).apply()
            val confirmed = booleanArrayOf(false)
            val latch = java.util.concurrent.CountDownLatch(1)
            runOnUiThread {
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("需要重启")
                    .setMessage("地址修改后需重启本 APP 生效。\n确定重启？取消则回滚地址。")
                    .setPositiveButton("重启") { _, _ ->
                        confirmed[0] = true; latch.countDown() }
                    .setNegativeButton("取消") { _, _ -> latch.countDown() }
                    .setOnCancelListener { latch.countDown() }
                    .show()
            }
            latch.await(120, java.util.concurrent.TimeUnit.SECONDS)
            return if (confirmed[0]) {
                runOnUiThread { finishAffinity() }
                val i = packageManager.getLaunchIntentForPackage(packageName)
                i?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TASK)
                startActivity(i)
                Runtime.getRuntime().exit(0)
                "restart"                       // exit 后不会真正返回
            } else {
                prefs.edit().putString("addr", old).apply()   // 回滚
                "cancel"
            }
        }

        @android.webkit.JavascriptInterface
        fun turnPort(): String =
            if (TurnService.listening) TurnService.port.toString() else ""

        @android.webkit.JavascriptInterface
        fun setTurnPort(p: String): Boolean =
            p.toIntOrNull()?.let { TurnService.restartOn(this@MainActivity, it) }
                ?: false

        @android.webkit.JavascriptInterface
        fun accEnabled(): Boolean {
            // 读系统真实启用状态：进程内绑定会因切后台/ROM 省电短暂解绑，
            // 若据此显示会误报「关」
            val am = getSystemService(ACCESSIBILITY_SERVICE) as
                android.view.accessibility.AccessibilityManager
            if (!am.isEnabled) return false
            return am.getEnabledAccessibilityServiceList(
                android.accessibilityservice.AccessibilityServiceInfo
                    .FEEDBACK_ALL_MASK
            ).any { svc ->
                svc.id?.contains(packageName) == true &&
                    svc.id.contains("TurnAccessibilityService")
            }
        }

        @android.webkit.JavascriptInterface
        fun openAccSettings() {
            runOnUiThread {
                startActivity(
                    Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }

        @android.webkit.JavascriptInterface
        fun batteryWhitelisted(): Boolean {
            val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
            return pm.isIgnoringBatteryOptimizations(packageName)
        }

        @android.webkit.JavascriptInterface
        fun requestBatteryWhitelist() {
            runOnUiThread {
                try {
                    startActivity(Intent(
                        android.provider.Settings
                            .ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        android.net.Uri.parse("package:$packageName")))
                } catch (e: Exception) {
                    startActivity(Intent(
                        android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                }
            }
        }
    }
}

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
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject

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
            // 网页里的「下载 APP」在 APP 内点击时交给系统浏览器下载
            setDownloadListener { url, _, _, _, _ ->
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url)))
                } catch (e: Exception) {
                }
            }
            webViewClient = object : WebViewClient() {
                private fun fail(msg: String) {
                    hadError = true
                    showStatus(msg, true)
                    welcome.visibility = ViewGroup.VISIBLE
                }

                // 主框架加载失败：必须拦——WebView 对失败加载同样回调
                // onPageFinished，不置 hadError 欢迎页会被错误地藏掉，
                // 整个 APP 只剩 WebView 深色底（全黑）
                override fun onReceivedError(view: WebView?,
                                             request: WebResourceRequest?,
                                             error: WebResourceError?) {
                    if (request?.isForMainFrame == true)
                        fail("加载失败（${error?.errorCode}）：${error?.description}")
                }

                // 旧系统兼容过载（minSdk 26 实际不走，防御性保留）
                override fun onReceivedError(view: WebView?, errorCode: Int,
                                             description: String?, failingUrl: String?) {
                    fail("加载失败（$errorCode）：$description")
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
                    // 记录「已验证接通」的地址：下次启动自动直连。端口自动
                    // 纠正（switchToAppPort）后的正确地址会在此覆盖旧值，
                    // 两者不会错位
                    prefs().edit().putString("addrOkUrl", normUrl(url)).apply()
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
                text = "Cube Remote"
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
            addView(bigButton("连 接").apply {
                setOnClickListener { doConnect() }
            }, LinearLayout.LayoutParams(
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
        // 自动直连：仅当保存地址与「上次验证接通的地址」一致（含端口自动
        // 纠正后的正确地址）。未验证过/失败过的地址留在欢迎页等用户按
        // 「连接」——失败时欢迎页与错误提示照常保留，不会黑屏
        val saved = prefs().getString("addr", null)?.trimEnd('/')
        if (!saved.isNullOrEmpty() && saved == prefs().getString("addrOkUrl", null)) {
            connect(saved)
        }
    }

    override fun onResume() {
        super.onResume()
    }

    private fun prefs() = getSharedPreferences("cube", MODE_PRIVATE)

    /** URL 归一（去尾部斜杠）：保存地址与验证地址的比较基准一致。 */
    private fun normUrl(u: String) = u.trimEnd('/')

    private fun dp(v: Int) = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun bigButton(text: String): Button =
        Button(this).apply {
            this.text = text
            textSize = 16f
            minHeight = dp(48)
            setTextColor(Color.BLACK)
            // ripple 按压反馈：自定义背景后系统默认按压态会消失
            val shape = GradientDrawable().apply {
                setColor(0xFF7fe896.toInt())
                cornerRadius = dp(12).toFloat()
            }
            background = android.graphics.drawable.RippleDrawable(
                android.content.res.ColorStateList.valueOf(0x33000000),
                shape, shape)
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
        try {
            val addr = addrInput.text.toString().trim()
            if (addr.isEmpty()) {
                showStatus("请输入电脑地址", true)
                return
            }
            val url = if ("://" in addr) addr else "http://$addr"
            prefs().edit().putString("addr", url).apply()
            connect(url)
        } catch (e: Exception) {
            showStatus("连接异常：${e.message}", true)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // 返回手势/返回键优先关页面浮层（设置面板、谱面 App 选择等，
        // uiBack 关最上层 .mask.show）；没关掉任何浮层才走默认行为。
        // evaluateJavascript 异步回调在主线程，按结果补默认动作
        webView.evaluateJavascript(
            "(typeof uiBack==='function')&&uiBack()==='true'") { hit ->
            if (hit != "true") {
                if (webView.canGoBack()) webView.goBack()
                else moveTaskToBack(true)
            }
        }
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        mainHandler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    /** JS 桥：设置面板的「连接设置」落到原生层（改地址需重启 APP）。
     *  方法在 JS 桥线程执行；弹确认框切主线程并阻塞等用户选择。 */
    inner class Bridge {

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
        fun turnMethod(): String =
            getSharedPreferences("cube", MODE_PRIVATE)
                .getString("turnMethod", "tap") ?: "tap"

        @android.webkit.JavascriptInterface
        fun setTurnMethod(m: String): Boolean {
            if (m !in setOf("tap", "double", "swipe", "media")) return false
            getSharedPreferences("cube", MODE_PRIVATE)
                .edit().putString("turnMethod", m).apply()
            return true
        }

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
        fun usageAccess(): Boolean = try {
            val appOps = getSystemService(APP_OPS_SERVICE) as android.app.AppOpsManager
            appOps.checkOpNoThrow(
                android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(), packageName) ==
                android.app.AppOpsManager.MODE_ALLOWED
        } catch (e: Exception) {
            false
        }

        @android.webkit.JavascriptInterface
        fun turnTarget(): String =
            prefs().getString("turnTargetName", "") ?: ""

        @android.webkit.JavascriptInterface
        fun setTurnTarget(pkg: String, name: String) {
            prefs().edit()
                .putString("turnTargetPkg", pkg)
                .putString("turnTargetName", name).apply()
        }

        @android.webkit.JavascriptInterface
        fun clearTurnTarget() {
            prefs().edit()
                .remove("turnTargetPkg").remove("turnTargetName").apply()
        }

        @android.webkit.JavascriptInterface
        fun listApps(): String {
            val intent = Intent(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
            val out = StringBuilder("[")
            val list = packageManager.queryIntentActivities(intent, 0)
                .filter { it.activityInfo.packageName != packageName }
                .sortedBy { it.loadLabel(packageManager).toString() }
            list.forEachIndexed { idx, info ->
                if (idx > 0) out.append(',')
                out.append("{\"pkg\":")
                    .append(JSONObject.quote(info.activityInfo.packageName))
                    .append(",\"name\":")
                    .append(JSONObject.quote(info.loadLabel(packageManager).toString()))
                    .append('}')
            }
            out.append(']')
            return out.toString()
        }

        @android.webkit.JavascriptInterface
        fun openUsageAccess() {
            runOnUiThread {
                try {
                    startActivity(Intent(
                        android.provider.Settings.ACTION_USAGE_ACCESS_SETTINGS))
                } catch (e: Exception) {
                }
            }
        }

        @android.webkit.JavascriptInterface
        fun version(): String = try {
            packageManager.getPackageInfo(packageName, 0).versionName ?: "?"
        } catch (e: Exception) {
            "?"
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

        /** APP 误连网页端口时的自动纠正：改存地址并加载正确端口（同主机）。 */
        @android.webkit.JavascriptInterface
        fun switchToAppPort(port: String) {
            val p = port.toIntOrNull() ?: return
            if (p < 1024 || p > 65535) return
            val cur = prefs().getString("addr", "") ?: return
            val host = android.net.Uri.parse(cur).host ?: return
            val url = "http://$host:$p"
            if (url == cur) return
            runOnUiThread {
                prefs().edit().putString("addr", url).apply()
                addrInput.setText(url.removePrefix("http://"))
                webView.stopLoading()
                hadError = false
                loaded = false
                showStatus("已自动切换到 APP 端口 $p …", false)
                welcome.visibility = ViewGroup.VISIBLE
                webView.loadUrl(url)
            }
        }
    }
}

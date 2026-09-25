package com.cubemanager.turnpage

import android.Manifest
import android.app.Activity
import android.app.Dialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/** WebView 壳：加载电脑端 APP 版控制页（http://<ip>:8767/），页面代码零改动。
 *  未连接/失败时显示品牌连接页；地址记忆在 SharedPreferences。
 *  instance 供 TurnService 执行测试推送时的「切后台」动作。 */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var welcome: LinearLayout
    private lateinit var webView: WebView
    private var hadError = false

    companion object {
        @Volatile
        var instance: MainActivity? = null
            private set
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instance = this
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        status = TextView(this).apply {
            setPadding(48, 20, 48, 20)
            setTextColor(0xFF9aa0aa.toInt())
        }
        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            setBackgroundColor(0xFF14161A.toInt())
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(view: WebView?, errorCode: Int,
                                             description: String?, failingUrl: String?) {
                    hadError = true
                    showWelcome("加载失败（$errorCode）")
                }
                override fun onPageFinished(view: WebView?, url: String?) {
                    if (!hadError && url != null && !url.startsWith("about:"))
                        welcome.visibility = ViewGroup.GONE
                }
            }
        }
        val connBtn = bigButton("连接电脑")
        connBtn.setOnClickListener { promptAddress() }
        welcome = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(0xFF14161A.toInt())
            addView(TextView(this@MainActivity).apply {
                text = "Cube 翻谱"
                textSize = 34f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(0xFF7fe896.toInt())
            })
            addView(TextView(this@MainActivity).apply {
                text = "连接电脑端 Cube Setlist Manager"
                setTextColor(0xFF9aa0aa.toInt())
                setPadding(0, 24, 0, 48)
            })
            addView(status)
            addView(connBtn)
        }
        val root = FrameLayout(this).apply {
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
        startForegroundService(Intent(this, TurnService::class.java))
        val saved = prefs().getString("addr", null)
        if (saved.isNullOrEmpty()) showWelcome() else webView.loadUrl(saved)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun prefs() = getSharedPreferences("cube", MODE_PRIVATE)

    private fun dp(v: Int) = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun bigButton(text: String): Button =
        Button(this).apply {
            this.text = text
            textSize = 16f
            setTextColor(Color.BLACK)
            background = GradientDrawable().apply {
                setColor(0xFF7fe896.toInt())
                cornerRadius = dp(12).toFloat()
            }
            setPadding(dp(40), dp(14), dp(40), dp(14))
        }

    private fun showWelcome(err: String? = null) {
        hadError = err != null
        status.text = when {
            err != null -> "$err\n点「连接电脑」重试"
            else -> "未连接：输入电脑端设置页显示的网页地址"
        }
        status.setTextColor(if (err != null) 0xFFB00020.toInt()
                            else 0xFF9aa0aa.toInt())
        welcome.visibility = ViewGroup.VISIBLE
    }

    private fun refreshStatus() {
        val svcOn = TurnAccessibilityService.instance != null
        if (welcome.visibility == ViewGroup.VISIBLE && !hadError &&
            prefs().getString("addr", null) != null)
            showWelcome(if (svcOn) null else null)   // 回前台时保持品牌页文案
    }

    private fun promptAddress() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(0xFF1e2127.toInt())
                cornerRadius = dp(18).toFloat()
            }
            setPadding(dp(24), dp(24), dp(24), dp(20))
            addView(TextView(this@MainActivity).apply {
                text = "连接电脑"
                textSize = 18f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(0xFFe9ebef.toInt())
            })
            addView(TextView(this@MainActivity).apply {
                text = "电脑端设置页「热点状态」里显示的地址"
                textSize = 13f
                setTextColor(0xFF8b909a.toInt())
                setPadding(0, dp(6), 0, dp(16))
            })
        }
        val input = EditText(this).apply {
            hint = "192.168.137.1:8767"
            // 热点 IP 固定，预填默认值（hint 不是值，别让用户误以为已填）
            setText("192.168.137.1:8767")
            setSingleLine(true)
            setTextColor(0xFFe9ebef.toInt())
            setHintTextColor(0xFF565b64.toInt())
            background = GradientDrawable().apply {
                setColor(0xFF262a32.toInt())
                cornerRadius = dp(10).toFloat()
            }
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        box.addView(input)
        val btn = bigButton("连 接")
        box.addView(btn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(18) })

        val dlg = Dialog(this)
        dlg.setContentView(box)
        dlg.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.86).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT)
        dlg.window?.setBackgroundDrawableResource(android.R.color.transparent)
        btn.setOnClickListener {
            val addr = input.text.toString().trim()
            if (addr.isEmpty()) {
                status.text = "未输入地址"
                return@setOnClickListener
            }
            val url = if ("://" in addr) addr else "http://$addr"
            prefs().edit().putString("addr", url).apply()
            hadError = false
            showWelcome("连接中 $url …")
            webView.loadUrl(url)
            dlg.dismiss()
        }
        dlg.show()
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else moveTaskToBack(true)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }
}

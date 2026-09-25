package com.cubemanager.turnpage

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** WebView 壳：加载电脑端现有控制页（http://<ip>:8765/），页面代码零改动。
 *  顶部状态条显示手势/无障碍状态；电脑地址记忆在 SharedPreferences。 */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        status = TextView(this).apply { setPadding(24, 14, 24, 14) }
        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            setBackgroundColor(0xFF14161A.toInt())
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(view: WebView?, errorCode: Int,
                                             description: String?, failingUrl: String?) {
                    showStatus("加载失败（$errorCode）：$description", err = true)
                }
                override fun onPageFinished(view: WebView?, url: String?) {
                    if (url != null && !url.startsWith("about:"))
                        showStatus("已连接 $url", err = false)
                }
            }
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(Button(this@MainActivity).apply {
                text = "电脑地址"
                setOnClickListener { promptAddress() }
            })
            addView(status)
            addView(webView, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0).apply { weight = 1f })
        }
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        startForegroundService(Intent(this, TurnService::class.java))
        val saved = prefs().getString("addr", null)
        if (saved.isNullOrEmpty()) {
            // 对话框不能在 onCreate 弹（窗口未就绪）；等首帧之后
            status.text = "未连接：点「电脑地址」输入电脑的网页地址"
            window.decorView.post { promptAddress() }
        } else {
            webView.loadUrl(saved)
            refreshStatus()
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun prefs() = getSharedPreferences("cube", MODE_PRIVATE)

    private fun showStatus(text: String, err: Boolean) {
        status.text = text
        status.setTextColor(if (err) 0xFFB00020.toInt() else 0xFF2E7D32.toInt())
    }

    private fun promptAddress() {
        val input = EditText(this).apply {
            hint = "192.168.137.1:8765"
            // 热点 IP 固定，预填默认值（hint 不是值，别让用户误以为已填）
            setText("192.168.137.1:8765")
            setSingleLine(true)
        }
        AlertDialog.Builder(this)
            .setTitle("电脑地址")
            .setMessage("电脑端设置页「热点状态」里显示的网页地址")
            .setView(input)
            .setPositiveButton("连接") { _, _ ->
                val addr = input.text.toString().trim()
                if (addr.isEmpty()) {
                    showStatus("未输入地址，未连接", err = true)
                    return@setPositiveButton
                }
                val url = if ("://" in addr) addr else "http://$addr"
                prefs().edit().putString("addr", url).apply()
                webView.loadUrl(url)
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun refreshStatus() {
        val svcOn = TurnAccessibilityService.instance != null
        val gesture = if (svcOn) "手势就绪" else "无障碍未开启（仅媒体键可用）"
        val addr = prefs().getString("addr", "") ?: ""
        status.text = "$gesture · $addr"
        status.setTextColor(if (svcOn) 0xFF2E7D32.toInt() else 0xFFB00020.toInt())
    }
}

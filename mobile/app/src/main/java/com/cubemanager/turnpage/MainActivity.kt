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
            webViewClient = WebViewClient()
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
        load(prefs().getString("addr", null))
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun prefs() = getSharedPreferences("cube", MODE_PRIVATE)

    private fun load(addr: String?) {
        if (addr.isNullOrEmpty()) promptAddress() else webView.loadUrl(addr)
    }

    private fun promptAddress() {
        val input = EditText(this).apply {
            hint = "192.168.137.1:8765"
            setText(prefs().getString("addr", "")?.replace(Regex("^https?://"), ""))
            setSingleLine(true)
        }
        AlertDialog.Builder(this)
            .setTitle("电脑地址")
            .setMessage("电脑端设置页「热点状态」里显示的网页地址")
            .setView(input)
            .setPositiveButton("连接") { _, _ ->
                val addr = input.text.toString().trim()
                if (addr.isNotEmpty()) {
                    val url = if ("://" in addr) addr else "http://$addr"
                    prefs().edit().putString("addr", url).apply()
                    webView.loadUrl(url)
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun refreshStatus() {
        val svcOn = TurnAccessibilityService.instance != null
        status.text = if (svcOn) "手势就绪 · 电脑推送可用"
                      else "无障碍未开启：点按/滑动不可用（设置→无障碍→Cube 翻谱）"
        status.setTextColor(if (svcOn) 0xFF2E7D32.toInt() else 0xFFB00020.toInt())
    }
}

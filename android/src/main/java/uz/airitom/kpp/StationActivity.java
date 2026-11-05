package uz.airitom.kpp;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.net.Uri;
import android.print.PrintManager;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public final class StationActivity extends Activity {
    private WebView page;
    private final Uri origin = Uri.parse(BuildConfig.ORIGIN);

    private boolean trusted(Uri uri) {
        return uri != null && "https".equals(uri.getScheme())
            && origin.getEncodedAuthority().equalsIgnoreCase(uri.getEncodedAuthority());
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        page = new WebView(this);
        page.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        setContentView(page);
        WebSettings settings = page.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager.getInstance().setAcceptThirdPartyCookies(page, false);
        page.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("airitom:print".equals(uri.toString()) && request.isForMainFrame()) {
                    Uri current = Uri.parse(view.getUrl());
                    if (trusted(current) && current.getPath().matches("/zone/receipt/[0-9]+")) {
                        PrintManager printers = (PrintManager) getSystemService(PRINT_SERVICE);
                        printers.print("Airitom chek", view.createPrintDocumentAdapter("Airitom chek"), null);
                    }
                    return true;
                }
                return !trusted(uri);
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (trusted(Uri.parse(url)) && Uri.parse(url).getPath().matches("/zone/receipt/[0-9]+")) {
                    view.evaluateJavascript("window.print=function(){location.href='airitom:print'}", null);
                }
                CookieManager.getInstance().flush();
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError error) {
                if (!req.isForMainFrame() || isFinishing()) return;
                new AlertDialog.Builder(StationActivity.this).setMessage("Server bilan aloqa yo‘q. Avvalgi amal natijasini jurnaldan tekshiring.")
                    .setPositiveButton("Ish oynasiga qaytish", (dialog, which) -> openStation()).show();
            }
        });
        openStation();
    }

    private void openStation() { page.loadUrl(BuildConfig.ORIGIN + BuildConfig.STATION); }
    @Override public void onBackPressed() {
        if (page.canGoBack()) openStation(); else super.onBackPressed();
    }
}

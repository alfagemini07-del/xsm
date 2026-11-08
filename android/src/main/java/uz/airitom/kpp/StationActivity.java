package uz.airitom.kpp;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.print.PrintJob;
import android.print.PrintManager;
import android.util.Base64;
import android.webkit.*;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.view.WindowInsets;
import android.graphics.Color;
import android.widget.LinearLayout;
import android.widget.Toast;
import com.sunmi.peripheral.printer.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

public final class StationActivity extends Activity {
    private WebView page;
    private PermissionRequest pendingCamera;
    private final Uri origin=Uri.parse(BuildConfig.ORIGIN);
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final ScheduledExecutorService deadlines=Executors.newSingleThreadScheduledExecutor();
    private SharedPreferences preferences;
    private volatile SunmiPrinterService sunmi;
    private boolean printing=false;
    private String active="";
    private final InnerPrinterCallback connection=new InnerPrinterCallback(){
        @Override protected void onConnected(SunmiPrinterService service){sunmi=service;}
        @Override protected void onDisconnected(){sunmi=null;}
    };
    private boolean trusted(Uri uri){return uri!=null&&"https".equals(uri.getScheme())&&origin.getEncodedAuthority().equalsIgnoreCase(uri.getEncodedAuthority());}
    private boolean trustedPage(){return page!=null&&trusted(Uri.parse(page.getUrl()==null?"":page.getUrl()));}

    /**
     * Returns a 100% deterministic hardware identifier (IMEI / MEID / ANDROID_ID / Hardware Fingerprint)
     * that NEVER changes when the APK is updated or uninstalled and reinstalled on the same phone/terminal.
     */
    public String getHardwareId() {
        String id = null;
        try {
            android.telephony.TelephonyManager tm = (android.telephony.TelephonyManager) getSystemService(TELEPHONY_SERVICE);
            if (tm != null && checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED) {
                if (Build.VERSION.SDK_INT >= 26) {
                    try { id = tm.getImei(0); } catch (Throwable ignored) {}
                    if (id == null || id.isEmpty()) {
                        try { id = tm.getImei(); } catch (Throwable ignored) {}
                    }
                    if (id == null || id.isEmpty()) {
                        try { id = tm.getMeid(); } catch (Throwable ignored) {}
                    }
                } else {
                    id = tm.getDeviceId();
                }
            }
        } catch (Throwable ignored) {}

        if (id == null || id.isEmpty()) {
            try {
                if (Build.VERSION.SDK_INT < 26 && Build.SERIAL != null && !Build.SERIAL.equalsIgnoreCase("unknown")) {
                    id = Build.SERIAL;
                } else if (Build.VERSION.SDK_INT >= 26 && checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED) {
                    String s = Build.getSerial();
                    if (s != null && !s.equalsIgnoreCase("unknown")) id = s;
                }
            } catch (Throwable ignored) {}
        }

        String androidId = null;
        try {
            androidId = android.provider.Settings.Secure.getString(getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
        } catch (Throwable ignored) {}

        if (id == null || id.isEmpty()) {
            if (androidId != null && !androidId.isEmpty() && !"9774d56d682e549c".equalsIgnoreCase(androidId)) {
                id = androidId.toUpperCase(Locale.ROOT);
            }
        }

        // Deterministic hardware fingerprint fallback (survives APK uninstall/reinstall even without SIM or ANDROID_ID)
        if (id == null || id.isEmpty()) {
            String hwSeed = "airitom-hw:" + Build.BOARD + ":" + Build.BRAND + ":" + Build.DEVICE + ":"
                    + Build.HARDWARE + ":" + Build.MANUFACTURER + ":" + Build.MODEL + ":" + Build.PRODUCT;
            id = "HW-" + UUID.nameUUIDFromBytes(hwSeed.getBytes(StandardCharsets.UTF_8)).toString().replace("-", "").substring(0, 16).toUpperCase(Locale.ROOT);
        }

        return id.trim();
    }

    public String getDeviceModel() {
        String manufacturer = Build.MANUFACTURER != null ? Build.MANUFACTURER : "";
        String model = Build.MODEL != null ? Build.MODEL : "";
        if (model.toLowerCase(Locale.ROOT).startsWith(manufacturer.toLowerCase(Locale.ROOT))) {
            return model.trim();
        }
        return (manufacturer + " " + model).trim();
    }

    private void syncDeviceIdentityToWeb() {
        if (page == null) return;
        final String hwId = getHardwareId();
        final String model = getDeviceModel();
        WebSettings settings = page.getSettings();
        String baseUa = settings.getUserAgentString().replaceAll("\\s*AiritomApp/2\\.0\\s*\\([^)]*\\)", "");
        settings.setUserAgentString(baseUa + " AiritomApp/2.0 (Model/" + model + "; HWID/" + hwId + ")");

        android.webkit.CookieManager cookieManager = android.webkit.CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setCookie(origin.toString(), "xsm_hw_id=" + hwId + "; path=/; max-age=315360000; SameSite=Lax");
        try {
            cookieManager.setCookie(origin.toString(), "xsm_device_model=" + URLEncoder.encode(model, "UTF-8") + "; path=/; max-age=315360000; SameSite=Lax");
        } catch (Exception ignored) {}
        cookieManager.flush();
    }

    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        preferences=getSharedPreferences("printer",MODE_PRIVATE);
        String interrupted=preferences.getString("active",null);
        if(interrupted!=null)try{JSONObject job=new JSONObject(interrupted);job.put("state","unknown");job.put("detail","Dastur yopildi; qog‘ozni tekshiring. Avtomatik qayta chop qilinmadi.");save(job);}catch(Exception ignored){}
        preferences.edit().remove("active").commit();
        bindPrinter();

        if (Build.VERSION.SDK_INT >= 23 && Build.VERSION.SDK_INT < 29) {
            if (checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) {
                try { requestPermissions(new String[]{Manifest.permission.READ_PHONE_STATE}, 43); } catch (Throwable ignored) {}
            }
        }

        page=new WebView(this);
        FrameLayout content = new FrameLayout(this);
        content.setBackgroundColor(Color.rgb(14, 27, 44));
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            content.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets safe = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom);
                return WindowInsets.CONSUMED;
            });
        }
        content.addView(page, new FrameLayout.LayoutParams(-1, -1));
        setContentView(content);
        content.requestApplyInsets();

        WebSettings settings=page.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);

        syncDeviceIdentityToWeb();

        page.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public String getHardwareId() {
                return StationActivity.this.getHardwareId();
            }
            @JavascriptInterface
            public String getDeviceModel() {
                return StationActivity.this.getDeviceModel();
            }
        }, "XsmDevice");

        page.setWebChromeClient(new WebChromeClient(){
            @Override public void onPermissionRequest(PermissionRequest req){
                if (!trusted(req.getOrigin()) || !trustedPage() || !Arrays.asList(req.getResources()).contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                    req.deny();return;
                }
                if(checkSelfPermission(Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED){
                    req.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                }else if(pendingCamera==null){
                    pendingCamera=req;requestPermissions(new String[]{Manifest.permission.CAMERA},42);
                }else req.deny();
            }
            @Override public void onPermissionRequestCanceled(PermissionRequest req){if(pendingCamera==req)pendingCamera=null;}
        });
        page.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                Uri uri=request.getUrl();
                if("airitom".equals(uri.getScheme())){
                    if(!request.isForMainFrame()||!trustedPage())return true;
                    String action=uri.getSchemeSpecificPart().split("\\?")[0];
                    Uri query=Uri.parse("https://local/"+uri.getSchemeSpecificPart());
                    if("printer".equals(action))settings();
                    if("print-ack".equals(action))acknowledge(query.getQueryParameter("id"), query.getQueryParameter("state"));
                    if("print".equals(action))begin(query.getQueryParameter("attempt_id"),query.getQueryParameter("eid"));
                    return true;
                }
                return !trusted(uri);
            }
            @Override public void onPageFinished(WebView view,String url){
                if(trusted(Uri.parse(url))){
                    String curHw = getHardwareId();
                    String curModel = getDeviceModel();
                    view.evaluateJavascript("window.XsmNativePrinter=true;window.XsmNativeDevice={hwId:'" + curHw + "',model:'" + curModel.replace("'", "\\'") + "'};try{localStorage.setItem('xsm_hw_id','" + curHw + "');}catch(e){}window.dispatchEvent(new Event('xsm-printer-ready'));",null);
                    replay();
                }
                android.webkit.CookieManager.getInstance().flush();
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError error){
                if(req.isForMainFrame()&&!isFinishing())new AlertDialog.Builder(StationActivity.this).setMessage("Server bilan aloqa yo‘q. Saqlangan amal va chop natijasi aloqa tiklanganda tekshiriladi.").setPositiveButton("Qayta ulanish",(d,w)->openStation()).show();
            }
        });
        openStation();
    }
    private void bindPrinter(){try{InnerPrinterManager.getInstance().bindService(this,connection);}catch(Exception ignored){}}
    private void openStation(){
        syncDeviceIdentityToWeb();
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Device-HWID", getHardwareId());
        headers.put("X-Device-Model", getDeviceModel());
        page.loadUrl(BuildConfig.ORIGIN + BuildConfig.STATION, headers);
    }
    private void save(JSONObject job){preferences.edit().putString("result-"+job.optString("attempt_id"),job.toString()).commit();}
    private void acknowledge(String id, String state) {
        if (id == null || state == null) return;
        String key = "result-" + id;
        try {
            JSONObject saved = new JSONObject(preferences.getString(key, "{}"));
            if (state.equals(saved.optString("state"))) preferences.edit().remove(key).commit();
        } catch (JSONException ignored) {}
    }
    private void replay(){if(!trustedPage())return;for(Map.Entry<String,?> item:preferences.getAll().entrySet())if(item.getKey().startsWith("result-"))try{deliver(new JSONObject(String.valueOf(item.getValue())));}catch(Exception ignored){}}
    private void deliver(JSONObject job){if(!trustedPage())return;page.evaluateJavascript("window.dispatchEvent(new CustomEvent('xsm-print-result',{detail:"+job.toString()+"}));",null);}
    private JSONObject job(String id,String eid,String method,String state,String detail){JSONObject j=new JSONObject();try{j.put("attempt_id",id);j.put("eid",Long.parseLong(eid));j.put("method",method);j.put("state",state);j.put("detail",detail);}catch(Exception ignored){}return j;}
    private void finish(String id,String eid,String method,String state,String detail){ui.post(()->{
        if (!active.equals(id) && !"confirmed".equals(state) && !"failed".equals(state)) return;
        if (id.equals(preferences.getString("last-confirmed", "")) && !"confirmed".equals(state)) return;
        if ("confirmed".equals(state)) preferences.edit().putString("last-confirmed",id).commit();
        JSONObject result=job(id,eid,method,state,detail);save(result);deliver(result);
        if(active.equals(id)&&!state.equals("sent")){printing=false;active="";preferences.edit().remove("active").commit();}
    });}
    private void begin(String id,String eid){
        try{UUID.fromString(id);Long.parseLong(eid);}catch(Exception e){return;}
        if(!("/zone/receipt/"+eid).equals(Uri.parse(page.getUrl()).getPath()))return;
        String method=preferences.getString("method","internal");
        if(printing){finish(id,eid,method,"failed","Boshqa chek chop etilmoqda. Uning tugashini kuting.");return;}
        printing=true;active=id;preferences.edit().putString("active",job(id,eid,method,"requested","").toString()).commit();
        if(method.equals("android")){androidPrint(id,eid);return;}
        page.evaluateJavascript("document.getElementById('receipt-image').src",value->{
            try{
                String data=(String)new JSONTokener(value).nextValue();
                if(!data.startsWith("data:image/png;base64,")||data.length()>1500000)throw new IOException("Chek rasmi olinmadi");
                byte[] png=Base64.decode(data.substring(data.indexOf(',')+1),Base64.DEFAULT);
                Bitmap bitmap=BitmapFactory.decodeByteArray(png,0,png.length);
                if(bitmap==null||bitmap.getWidth()>1000||bitmap.getHeight()>5000)throw new IOException("Chek o‘lchami noto‘g‘ri");
                worker.execute(()->print(bitmap,id,eid,method));
            }catch(Exception e){finish(id,eid,method,"failed","Chek rasmi olinmadi. Qayta oching.");}
        });
    }
    private void print(Bitmap original,String id,String eid,String method){
        int width=method.equals("internal")?384:preferences.getInt("width",384);
        Bitmap bitmap=Bitmap.createScaledBitmap(original,width,Math.round(original.getHeight()*(float)width/original.getWidth()),false);
        if(method.equals("internal")){sunmiPrint(bitmap,id,eid);return;}
        try{
            byte[] data=raster(bitmap);
            if(method.equals("wifi")){
                String host=preferences.getString("host","").trim();
                int port=preferences.getInt("port",9100);
                if(host.isEmpty())throw new IOException("Wi-Fi printer IP manzili kiritilmagan");
                try(Socket socket=new Socket()){
                    socket.connect(new InetSocketAddress(host,port),5000);
                    socket.setSoTimeout(8000);
                    OutputStream out=socket.getOutputStream();
                    out.write(data);out.flush();
                }
            }else if(method.equals("bluetooth")){
                String address=preferences.getString("bt","").trim();
                if(address.isEmpty())throw new IOException("Bluetooth printer tanlanmagan");
                BluetoothAdapter adapter=BluetoothAdapter.getDefaultAdapter();
                if(adapter==null)throw new IOException("Bluetooth mavjud emas");
                BluetoothDevice dev=adapter.getRemoteDevice(address);
                try(BluetoothSocket socket=dev.createRfcommSocketToServiceRecord(UUID.fromString("00001101-0000-1000-8000-00805F9B34FB"))){
                    socket.connect();
                    OutputStream out=socket.getOutputStream();
                    out.write(data);out.flush();
                }
            }
            finish(id,eid,method,"confirmed","");
        }catch(Exception e){finish(id,eid,method,"failed","Printer xatosi: "+e.getMessage());}
    }
    private void sunmiPrint(Bitmap bitmap,String id,String eid){
        SunmiPrinterService service=sunmi;
        if(service==null){finish(id,eid,"internal","failed","Ichki Sunmi printer topilmadi. Printer sozlamasini tekshiring.");return;}
        ScheduledFuture<?> timeout=deadlines.schedule(()->finish(id,eid,"internal","unknown","Printer javobi kechikdi. Qog‘ozni tekshiring."),12,TimeUnit.SECONDS);
        try{
            service.printBitmap(bitmap,new InnerResultCallback(){
                @Override public void onRunResult(boolean isSuccess){if(!isSuccess){timeout.cancel(false);finish(id,eid,"internal","failed","Ichki printer chop qilmadi.");}}
                @Override public void onReturnString(String result){}
                @Override public void onRaiseException(int code,String msg){timeout.cancel(false);finish(id,eid,"internal","failed","Printer xatosi ("+code+"): "+msg);}
                @Override public void onPrintResult(int code,String msg){timeout.cancel(false);if(code==0)finish(id,eid,"internal","confirmed","");else finish(id,eid,"internal","failed","Qog‘oz yoki qopqoqni tekshiring ("+msg+")");}
            });
            service.lineWrap(4,null);
        }catch(Exception e){timeout.cancel(false);finish(id,eid,"internal","failed","Ichki printer xatosi: "+e.getMessage());}
    }
    private void androidPrint(String id,String eid){
        PrintManager pm=(PrintManager)getSystemService(PRINT_SERVICE);
        if(pm==null){finish(id,eid,"android","failed","Android Print xizmati mavjud emas");return;}
        PrintJob pj=pm.print("Airitom-Receipt-"+eid,page.createPrintDocumentAdapter("Airitom-Receipt-"+eid),null);
        finish(id,eid,"android","sent","Tizim chop oynasi ochildi");
        deadlines.schedule(new Runnable(){
            int checks=0;
            @Override public void run(){
                ui.post(()->{
                    if(pj.isCompleted())finish(id,eid,"android","confirmed","");
                    else if(pj.isFailed())finish(id,eid,"android","failed","Android printer xatosi");
                    else if(pj.isCancelled())finish(id,eid,"android","cancelled","Chop etish bekor qilindi");
                    else if(++checks<60)deadlines.schedule(this,2,TimeUnit.SECONDS);
                    else finish(id,eid,"android","unknown","Chop holati noma’lum; qog‘ozni tekshiring");
                });
            }
        },2,TimeUnit.SECONDS);
    }
    private byte[] raster(Bitmap bmp) throws IOException {
        int w=bmp.getWidth(),h=bmp.getHeight(),bytes=(w+7)/8;
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        out.write(new byte[]{0x1B,0x40,0x1B,0x61,0x01,0x1D,0x76,0x30,0x00,(byte)(bytes&0xFF),(byte)((bytes>>8)&0xFF),(byte)(h&0xFF),(byte)((h>>8)&0xFF)});
        int[] pixels=new int[w*h];bmp.getPixels(pixels,0,w,0,0,w,h);
        for(int y=0;y<h;y++){
            for(int b=0;b<bytes;b++){
                int slice=0;
                for(int bit=0;bit<8;bit++){
                    int x=b*8+bit;
                    if(x<w){
                        int c=pixels[y*w+x];
                        int lum=(Color.red(c)*299+Color.green(c)*587+Color.blue(c)*114)/1000;
                        if(Color.alpha(c)>128&&lum<165)slice|=(1<<(7-bit));
                    }
                }
                out.write(slice);
            }
        }
        out.write(new byte[]{0x0A,0x0A,0x0A,0x0A,0x1D,0x56,0x42,0x00});
        return out.toByteArray();
    }
    private void settings(){
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(48,32,48,16);
        final String[] methods={"internal","wifi","bluetooth","android"};
        final String[] labels={"Ichki Sunmi printer","Wi-Fi / LAN ESC-POS (9100)","Bluetooth ESC-POS","Android tizim printeri"};
        String current=preferences.getString("method","internal");
        int checked=Math.max(0,Arrays.asList(methods).indexOf(current));
        final EditText host=new EditText(this);host.setHint("Wi-Fi printer IP (masalan 192.168.1.200)");host.setText(preferences.getString("host",""));
        final EditText bt=new EditText(this);bt.setHint("Bluetooth MAC (masalan AA:BB:CC:DD:EE:FF)");bt.setText(preferences.getString("bt",""));
        box.addView(host);box.addView(bt);
        new AlertDialog.Builder(this).setTitle("Printer sozlamalari (ID: " + getHardwareId() + ")")
            .setSingleChoiceItems(labels,checked,(d,which)->preferences.edit().putString("method",methods[which]).apply())
            .setView(box)
            .setPositiveButton("Saqlash",(d,w)->{
                preferences.edit().putString("host",host.getText().toString().trim()).putString("bt",bt.getText().toString().trim()).apply();
                Toast.makeText(this,"Printer sozlamasi saqlandi",Toast.LENGTH_SHORT).show();
            }).setNegativeButton("Yopish",null).show();
    }
    @Override public void onRequestPermissionsResult(int code,String[] perms,int[] res){
        if(code==42 && pendingCamera!=null){
            if(res.length>0&&res[0]==PackageManager.PERMISSION_GRANTED)pendingCamera.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else pendingCamera.deny();
            pendingCamera=null;
        } else if (code==43) {
            syncDeviceIdentityToWeb();
        }
    }
    @Override public void onBackPressed(){if(page!=null&&page.canGoBack())page.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){try{InnerPrinterManager.getInstance().unBindService(this,connection);}catch(Exception ignored){}worker.shutdownNow();deadlines.shutdownNow();super.onDestroy();}
}

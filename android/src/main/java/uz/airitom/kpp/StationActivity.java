package uz.airitom.kpp;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.SharedPreferences;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
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
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.Spinner;
import android.widget.TextView;
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
    private static final String USB_PERMISSION="uz.airitom.kpp.USB_PERMISSION";
    private boolean printing=false;
    private String active="";
    private final InnerPrinterCallback connection=new InnerPrinterCallback(){
        @Override protected void onConnected(SunmiPrinterService service){sunmi=service;}
        @Override protected void onDisconnected(){sunmi=null;ui.postDelayed(()->{if(!isFinishing())bindPrinter();},1200);}
    };
    private final BroadcastReceiver usbPermissionReceiver=new BroadcastReceiver(){
        @Override public void onReceive(Context context,Intent intent){
            if(!USB_PERMISSION.equals(intent.getAction()))return;
            UsbDevice device=Build.VERSION.SDK_INT>=33?intent.getParcelableExtra(UsbManager.EXTRA_DEVICE,UsbDevice.class):intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            boolean granted=intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED,false);
            Toast.makeText(StationActivity.this,granted?"USB printerga ruxsat berildi":"USB printer ruxsati berilmadi",Toast.LENGTH_SHORT).show();
            if(granted&&device!=null)preferences.edit().putString("usb",usbKey(device)).apply();
        }
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
        IntentFilter usbFilter=new IntentFilter(USB_PERMISSION);
        if(Build.VERSION.SDK_INT>=33)registerReceiver(usbPermissionReceiver,usbFilter,Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(usbPermissionReceiver,usbFilter);
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
    private String usbKey(UsbDevice device){return device.getVendorId()+":"+device.getProductId();}
    private UsbDevice selectedUsb(){
        UsbManager manager=(UsbManager)getSystemService(USB_SERVICE);if(manager==null)return null;
        String wanted=preferences.getString("usb","");
        for(UsbDevice device:manager.getDeviceList().values())if(wanted.equals(usbKey(device)))return device;
        return null;
    }
    private String automaticMethod(){
        if(sunmi!=null)return "internal";
        UsbManager usb=(UsbManager)getSystemService(USB_SERVICE);UsbDevice device=selectedUsb();
        if(device!=null&&usb!=null&&usb.hasPermission(device))return "usb";
        if(!preferences.getString("bt","").isEmpty())return "bluetooth";
        if(!preferences.getString("host","").isEmpty())return "wifi";
        return "android";
    }
    private void usbWrite(byte[] data)throws IOException{
        UsbManager manager=(UsbManager)getSystemService(USB_SERVICE);UsbDevice device=selectedUsb();
        if(manager==null||device==null)throw new IOException("USB printer tanlanmagan");
        if(!manager.hasPermission(device))throw new IOException("USB printer ruxsatini Printer sozlamasidan bering");
        UsbInterface chosen=null;UsbEndpoint output=null;
        for(int i=0;i<device.getInterfaceCount()&&output==null;i++){
            UsbInterface candidate=device.getInterface(i);
            for(int j=0;j<candidate.getEndpointCount();j++){
                UsbEndpoint endpoint=candidate.getEndpoint(j);
                if(endpoint.getDirection()==UsbConstants.USB_DIR_OUT&&endpoint.getType()==UsbConstants.USB_ENDPOINT_XFER_BULK){chosen=candidate;output=endpoint;break;}
            }
        }
        if(chosen==null||output==null)throw new IOException("USB printer OUT kanali topilmadi");
        UsbDeviceConnection connection=manager.openDevice(device);
        try{
            if(connection==null||!connection.claimInterface(chosen,true))throw new IOException("USB printer ochilmadi");
            if(chosen.getInterfaceClass()==UsbConstants.USB_CLASS_COMM){
                byte[] line={0x00,(byte)0xc2,0x01,0x00,0x00,0x00,0x08};
                connection.controlTransfer(0x21,0x20,0,chosen.getId(),line,line.length,1500);
            }
            for(int offset=0;offset<data.length;){int length=Math.min(4096,data.length-offset);int sent=connection.bulkTransfer(output,data,offset,length,5000);if(sent<=0)throw new IOException("USB printer javob bermadi");offset+=sent;}
            connection.releaseInterface(chosen);
        }finally{
            if(connection!=null)connection.close();
        }
    }
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
    private static final class ReceiptContent{
        final Bitmap bitmap;final byte[] direct;
        ReceiptContent(Bitmap bitmap,byte[] direct){this.bitmap=bitmap;this.direct=direct;}
    }
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
        String method=preferences.getString("method","auto");
        if(printing){finish(id,eid,method,"failed","Boshqa chek chop etilmoqda. Uning tugashini kuting.");return;}
        printing=true;active=id;preferences.edit().putString("active",job(id,eid,method,"requested","").toString()).commit();
        if(method.equals("android")){androidPrint(id,eid);return;}
        String script="JSON.stringify({image:(document.getElementById('receipt-image')?.src||''),qr:(document.getElementById('receipt-qr')?.src||''),fields:Array.from(document.querySelectorAll('#receipt-fields dt')).map((n,i)=>[n.textContent,document.querySelectorAll('#receipt-fields dd')[i]?.textContent||''])})";
        page.evaluateJavascript(script,value->{
            try{
                String json=(String)new JSONTokener(value).nextValue();
                JSONObject payload=new JSONObject(json);JSONArray fields=payload.optJSONArray("fields");
                if(fields==null||fields.length()==0)throw new IOException("Chek maydonlari olinmadi");
                int width=preferences.getInt("width",384);
                Bitmap bitmap=decodeDataBitmap(payload.optString("image"));
                boolean hasServerArtwork=bitmap!=null;
                if(bitmap==null)bitmap=fallbackBitmap(fields,payload.optString("qr"),width);
                byte[] direct=hasServerArtwork?null:directReceipt(fields);
                Bitmap finalBitmap=bitmap;
                worker.execute(()->print(new ReceiptContent(finalBitmap,direct),id,eid,method));
            }catch(Exception e){finish(id,eid,method,"failed","Chek ma’lumoti olinmadi: "+e.getMessage());}
        });
    }
    private Bitmap decodeDataBitmap(String data){
        try{
            if(data==null||!data.startsWith("data:image/png;base64,")||data.length()>1800000)return null;
            byte[] png=Base64.decode(data.substring(data.indexOf(',')+1),Base64.DEFAULT);
            Bitmap bitmap=BitmapFactory.decodeByteArray(png,0,png.length);
            return bitmap==null||bitmap.getWidth()>1200||bitmap.getHeight()>6000?null:bitmap;
        }catch(Exception ignored){return null;}
    }
    private Bitmap fallbackBitmap(JSONArray fields,String qrData,int width) throws JSONException{
        int height=Math.max(700,180+fields.length()*52+360);
        Bitmap result=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(result);canvas.drawColor(Color.WHITE);
        Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);paint.setColor(Color.BLACK);paint.setTypeface(Typeface.create(Typeface.MONOSPACE,Typeface.NORMAL));
        int y=38;paint.setTextAlign(Paint.Align.CENTER);paint.setTextSize(width>=576?30:22);paint.setTypeface(Typeface.DEFAULT_BOLD);canvas.drawText("AIRITOM LOGISTICS CENTER",width/2f,y,paint);y+=42;
        paint.setTypeface(Typeface.MONOSPACE);paint.setTextAlign(Paint.Align.LEFT);paint.setTextSize(width>=576?24:18);
        for(int i=0;i<fields.length();i++){
            JSONArray pair=fields.getJSONArray(i);String line=pair.optString(0)+": "+pair.optString(1);
            if(line.length()>46){canvas.drawText(line.substring(0,Math.min(46,line.length())),12,y,paint);y+=30;line=line.substring(Math.min(46,line.length()));}
            canvas.drawText(line,12,y,paint);y+=38;
        }
        Bitmap qr=decodeDataBitmap(qrData);
        if(qr!=null){int size=Math.min(width-70,280);Bitmap scaled=Bitmap.createScaledBitmap(qr,size,size,false);canvas.drawBitmap(scaled,(width-size)/2f,y+10,paint);y+=size+30;}
        paint.setTextAlign(Paint.Align.CENTER);paint.setTypeface(Typeface.DEFAULT_BOLD);canvas.drawText("Xizmatingiz uchun rahmat!",width/2f,y,paint);y+=35;
        return Bitmap.createBitmap(result,0,0,width,Math.min(result.getHeight(),y+20));
    }
    private byte[] directReceipt(JSONArray fields) throws IOException,JSONException{
        ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(new byte[]{0x1b,0x40,0x1b,0x61,0x01,0x1b,0x45,0x01});
        out.write("AIRITOM LOGISTICS CENTER\n".getBytes(StandardCharsets.US_ASCII));out.write(new byte[]{0x1b,0x45,0x00,0x1b,0x61,0x00});
        StringBuilder qrText=new StringBuilder("AIRITOM LOGISTICS CENTER\n");
        for(int i=0;i<fields.length();i++){
            JSONArray pair=fields.getJSONArray(i);String line=ascii(pair.optString(0))+": "+ascii(pair.optString(1));
            out.write((line+"\n").getBytes(StandardCharsets.US_ASCII));qrText.append(pair.optString(0)).append(": ").append(pair.optString(1)).append('\n');
        }
        out.write(new byte[]{0x1b,0x61,0x01});out.write(escposQr(qrText.toString().getBytes(StandardCharsets.UTF_8)));out.write("\n\n\n".getBytes(StandardCharsets.US_ASCII));out.write(new byte[]{0x1d,0x56,0x42,0x00});return out.toByteArray();
    }
    private String ascii(String value){return value.replace('‘','\'').replace('’','\'').replace('–','-').replace('—','-').replace("o‘","o'").replace("g‘","g'").replaceAll("[^\\x20-\\x7E]","?");}
    private byte[] escposQr(byte[] value)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(new byte[]{0x1d,0x28,0x6b,0x04,0x00,0x31,0x41,0x32,0x00,0x1d,0x28,0x6b,0x03,0x00,0x31,0x43,0x05,0x1d,0x28,0x6b,0x03,0x00,0x31,0x45,0x31});int n=value.length+3;out.write(new byte[]{0x1d,0x28,0x6b,(byte)(n&255),(byte)((n>>8)&255),0x31,0x50,0x30});out.write(value);out.write(new byte[]{0x1d,0x28,0x6b,0x03,0x00,0x31,0x51,0x30});return out.toByteArray();}
    private void print(ReceiptContent content,String id,String eid,String method){
        int width=preferences.getInt("width",384);
        Bitmap original=content.bitmap;
        Bitmap bitmap=Bitmap.createScaledBitmap(original,width,Math.round(original.getHeight()*(float)width/original.getWidth()),false);
        if(method.equals("auto"))method=automaticMethod();
        final String selectedMethod=method;
        if(method.equals("internal")){sunmiPrint(bitmap,id,eid);return;}
        try{
            byte[] data=content.direct!=null?content.direct:raster(bitmap);
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
                    ScheduledFuture<?> timeout=deadlines.schedule(()->{try{socket.close();}catch(Exception ignored){}},5,TimeUnit.SECONDS);
                    socket.connect();timeout.cancel(false);
                    OutputStream out=socket.getOutputStream();
                    out.write(data);out.flush();
                }
            }else if(method.equals("usb")){
                usbWrite(data);
            }else if(method.equals("android")){
                ui.post(()->androidPrint(id,eid));
                return;
            }else{
                throw new IOException("Printer turi tanlanmagan");
            }
            finish(id,eid,selectedMethod,"confirmed","");
        }catch(Exception e){finish(id,eid,selectedMethod,"failed","Printer xatosi: "+e.getMessage());}
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
        final String[] methods={"auto","internal","usb","bluetooth","wifi","android"};
        final String[] labels={"Avtomatik aniqlash (tavsiya)","Ichki SUNMI printer","USB / OTG / Serial ESC-POS","Bluetooth ESC-POS","Wi-Fi / LAN ESC-POS (9100)","Android tizim printeri"};
        final Spinner method=new Spinner(this);method.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,labels));
        int checked=Math.max(0,Arrays.asList(methods).indexOf(preferences.getString("method","auto")));method.setSelection(checked);box.addView(method);
        final String[] widths={"58 mm — 384 px","80 mm — 576 px"};
        final Spinner paper=new Spinner(this);paper.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,widths));paper.setSelection(preferences.getInt("width",384)==576?1:0);box.addView(paper);
        final EditText host=new EditText(this);host.setHint("Wi-Fi printer IP (masalan 192.168.1.200)");host.setText(preferences.getString("host",""));
        final TextView btState=new TextView(this);btState.setText("Bluetooth: "+(preferences.getString("bt","").isEmpty()?"tanlanmagan":preferences.getString("bt","")));
        final Button chooseBt=new Button(this);chooseBt.setText("Bluetooth printerni tanlash");chooseBt.setOnClickListener(v->chooseBluetooth(btState));
        final TextView usbState=new TextView(this);UsbDevice currentUsb=selectedUsb();usbState.setText("USB: "+(currentUsb==null?"tanlanmagan":currentUsb.getProductName()+" ("+usbKey(currentUsb)+")"));
        final Button chooseUsb=new Button(this);chooseUsb.setText("USB / OTG printerni tanlash");chooseUsb.setOnClickListener(v->chooseUsb(usbState));
        box.addView(host);box.addView(btState);box.addView(chooseBt);box.addView(usbState);box.addView(chooseUsb);
        new AlertDialog.Builder(this).setTitle("Printer sozlamalari (ID: " + getHardwareId() + ")")
            .setView(box)
            .setPositiveButton("Saqlash",(d,w)->{
                preferences.edit().putString("method",methods[method.getSelectedItemPosition()]).putInt("width",paper.getSelectedItemPosition()==1?576:384).putString("host",host.getText().toString().trim()).apply();
                Toast.makeText(this,"Printer sozlamasi saqlandi",Toast.LENGTH_SHORT).show();
            }).setNegativeButton("Yopish",null).show();
    }
    private void chooseBluetooth(TextView state){
        if(Build.VERSION.SDK_INT>=31&&checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT},44);return;}
        BluetoothAdapter adapter=BluetoothAdapter.getDefaultAdapter();if(adapter==null){Toast.makeText(this,"Bluetooth mavjud emas",Toast.LENGTH_SHORT).show();return;}
        List<BluetoothDevice> devices=new ArrayList<>(adapter.getBondedDevices());devices.sort(Comparator.comparing(d->String.valueOf(d.getName())));
        if(devices.isEmpty()){Toast.makeText(this,"Avval Android Bluetooth sozlamasida printerni juftlang",Toast.LENGTH_LONG).show();return;}
        String[] names=new String[devices.size()];for(int i=0;i<devices.size();i++)names[i]=(devices.get(i).getName()==null?"Printer":devices.get(i).getName())+" · "+devices.get(i).getAddress();
        new AlertDialog.Builder(this).setTitle("Bluetooth printer").setItems(names,(d,which)->{String address=devices.get(which).getAddress();preferences.edit().putString("bt",address).apply();state.setText("Bluetooth: "+names[which]);}).show();
    }
    private void chooseUsb(TextView state){
        UsbManager manager=(UsbManager)getSystemService(USB_SERVICE);if(manager==null)return;
        List<UsbDevice> devices=new ArrayList<>(manager.getDeviceList().values());
        if(devices.isEmpty()){Toast.makeText(this,"USB OTG printer topilmadi",Toast.LENGTH_LONG).show();return;}
        String[] names=new String[devices.size()];for(int i=0;i<devices.size();i++){UsbDevice d=devices.get(i);names[i]=(d.getProductName()==null?"USB printer":d.getProductName())+" · "+usbKey(d);}
        new AlertDialog.Builder(this).setTitle("USB / Serial printer").setItems(names,(dialog,which)->{
            UsbDevice device=devices.get(which);preferences.edit().putString("usb",usbKey(device)).apply();state.setText("USB: "+names[which]);
            if(!manager.hasPermission(device)){int flags=PendingIntent.FLAG_UPDATE_CURRENT|(Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0);PendingIntent pi=PendingIntent.getBroadcast(this,0,new Intent(USB_PERMISSION).setPackage(getPackageName()),flags);manager.requestPermission(device,pi);}
        }).show();
    }
    @Override public void onRequestPermissionsResult(int code,String[] perms,int[] res){
        if(code==42 && pendingCamera!=null){
            if(res.length>0&&res[0]==PackageManager.PERMISSION_GRANTED)pendingCamera.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else pendingCamera.deny();
            pendingCamera=null;
        } else if (code==43) {
            syncDeviceIdentityToWeb();
        } else if(code==44 && res.length>0 && res[0]==PackageManager.PERMISSION_GRANTED){
            settings();
        }
    }
    @Override public void onBackPressed(){if(page!=null&&page.canGoBack())page.goBack();else super.onBackPressed();}
    @Override protected void onResume(){super.onResume();if(sunmi==null)bindPrinter();}
    @Override protected void onDestroy(){try{unregisterReceiver(usbPermissionReceiver);}catch(Exception ignored){}try{InnerPrinterManager.getInstance().unBindService(this,connection);}catch(Exception ignored){}worker.shutdownNow();deadlines.shutdownNow();super.onDestroy();}
}

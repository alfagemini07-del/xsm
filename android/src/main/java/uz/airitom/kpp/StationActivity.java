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
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        preferences=getSharedPreferences("printer",MODE_PRIVATE);
        String interrupted=preferences.getString("active",null);
        if(interrupted!=null)try{JSONObject job=new JSONObject(interrupted);job.put("state","unknown");job.put("detail","Dastur yopildi; qog‘ozni tekshiring. Avtomatik qayta chop qilinmadi.");save(job);}catch(Exception ignored){}
        preferences.edit().remove("active").commit();
        bindPrinter();
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
        // Older Android versions resize the decor for the IME via adjustResize.
        content.addView(page, new FrameLayout.LayoutParams(-1, -1));
        setContentView(content);
        content.requestApplyInsets();
        WebSettings settings=page.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        android.webkit.CookieManager.getInstance().setAcceptThirdPartyCookies(page,false);
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
                if(trusted(Uri.parse(url))){view.evaluateJavascript("window.XsmNativePrinter=true;window.dispatchEvent(new Event('xsm-printer-ready'));",null);replay();}
                android.webkit.CookieManager.getInstance().flush();
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError error){
                if(req.isForMainFrame()&&!isFinishing())new AlertDialog.Builder(StationActivity.this).setMessage("Server bilan aloqa yo‘q. Saqlangan amal va chop natijasi aloqa tiklanganda tekshiriladi.").setPositiveButton("Qayta ulanish",(d,w)->openStation()).show();
            }
        });
        openStation();
    }
    private void bindPrinter(){try{InnerPrinterManager.getInstance().bindService(this,connection);}catch(Exception ignored){}}
    private void openStation(){page.loadUrl(BuildConfig.ORIGIN+BuildConfig.STATION);}
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
                try(Socket socket=new Socket()){
                    socket.connect(new InetSocketAddress(preferences.getString("host",""),preferences.getInt("port",9100)),6000);
                    ScheduledFuture<?> limit=deadlines.schedule(()->{try{socket.close();}catch(Exception ignored){}},20,TimeUnit.SECONDS);
                    try{socket.getOutputStream().write(data);socket.getOutputStream().flush();}finally{limit.cancel(false);}
                }
            }else if(method.equals("bluetooth")){
                if(Build.VERSION.SDK_INT>=31&&checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED)throw new IOException("Printer sozlamasida Bluetooth ruxsatini bering");
                BluetoothAdapter adapter=BluetoothAdapter.getDefaultAdapter();if(adapter==null||!adapter.isEnabled())throw new IOException("Bluetoothni yoqing");
                BluetoothDevice device=adapter.getRemoteDevice(preferences.getString("address",""));
                try(BluetoothSocket socket=device.createRfcommSocketToServiceRecord(UUID.fromString("00001101-0000-1000-8000-00805F9B34FB"))){
                    ScheduledFuture<?> limit=deadlines.schedule(()->{try{socket.close();}catch(Exception ignored){}},20,TimeUnit.SECONDS);
                    try{socket.connect();socket.getOutputStream().write(data);socket.getOutputStream().flush();}finally{limit.cancel(false);}
                }
            }else throw new IOException("Printer tanlanmagan");
            finish(id,eid,method,"sent","Printerga yuborildi; qog‘oz natijasi hali tasdiqlanmagan");
            ui.post(()->{if(!isFinishing())new AlertDialog.Builder(this).setTitle("Chek qog‘ozda to‘liq chiqdimi?").setMessage("Qayta chop etishdan avval qog‘ozni tekshiring.").setPositiveButton("Ha, chiqdi",(d,w)->finish(id,eid,method,"confirmed","Xodim qog‘oz chiqqanini tasdiqladi")).setNegativeButton("Chiqmadi",(d,w)->finish(id,eid,method,"failed","Xodim chek chiqmaganini bildirdi")).setOnCancelListener(d->finish(id,eid,method,"unknown","Qog‘oz natijasi tasdiqlanmagan")).show();});
        }catch(Exception e){finish(id,eid,method,"unknown","Ulanish xatosi: "+e.getMessage()+". Qayta chopdan avval qog‘ozni tekshiring.");}
    }
    private void sunmiPrint(Bitmap bitmap,String id,String eid){
        SunmiPrinterService service=sunmi;
        if(service==null){bindPrinter();finish(id,eid,"internal","failed","SUNMI printer xizmati ulanmagan. Bir necha soniyadan keyin qayta bosing.");return;}
        try{
            int status=service.updatePrinterState();
            if(status!=1)throw new IOException(printerStatus(status));
            service.enterPrinterBuffer(true);service.setAlignment(1,null);
            for(int top=0;top<bitmap.getHeight();top+=192){Bitmap strip=Bitmap.createBitmap(bitmap,0,top,bitmap.getWidth(),Math.min(192,bitmap.getHeight()-top));service.printBitmap(strip,null);}
            service.lineWrap(3,null);
            service.exitPrinterBufferWithCallback(true,new InnerResultCallback(){
                @Override public void onRunResult(boolean success){}
                @Override public void onReturnString(String result){}
                @Override public void onRaiseException(int code,String message){finish(id,eid,"internal","failed",printerStatus(code)+": "+message);}
                @Override public void onPrintResult(int code,String message){finish(id,eid,"internal",code==0?"confirmed":"failed",message==null?"SUNMI natijasi: "+code:message);}
            });
            ui.postDelayed(()->{if(active.equals(id))finish(id,eid,"internal","unknown","SUNMI javobi kelmadi. Qog‘ozni tekshiring; avtomatik qayta chop qilinmadi.");},45000);
        }catch(Exception e){try{service.exitPrinterBuffer(false);}catch(Exception ignored){}finish(id,eid,"internal","failed",e.getMessage());}
    }
    private String printerStatus(int code){switch(code){case 3:return "Printer bilan aloqa yo‘q";case 4:return "Qog‘oz tugagan";case 5:return "Printer qizib ketgan";case 6:return "Printer qopqog‘ini yoping";case 7:return "Kesish moslamasi xatosi";case 8:return "Qog‘oz holatini tekshiring";case 9:return "Qora belgi topilmadi";default:return "Printer holati: "+code;}}
    private byte[] raster(Bitmap bitmap)throws IOException{
        ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(new byte[]{27,64,27,97,1});
        int bytes=(bitmap.getWidth()+7)/8;
        for(int top=0;top<bitmap.getHeight();top+=192){int height=Math.min(192,bitmap.getHeight()-top);out.write(new byte[]{29,118,48,0,(byte)bytes,(byte)(bytes>>8),(byte)height,(byte)(height>>8)});
            for(int y=top;y<top+height;y++)for(int x=0;x<bytes;x++){int bits=0;for(int k=0;k<8;k++){int px=x*8+k;if(px<bitmap.getWidth()){int color=bitmap.getPixel(px,y);int lum=(((color>>16)&255)*299+((color>>8)&255)*587+(color&255)*114)/1000;if(lum<160)bits|=128>>k;}}out.write(bits);}}
        out.write(new byte[]{10,10,10});return out.toByteArray();
    }
    private void androidPrint(String id,String eid){
        PrintManager manager=(PrintManager)getSystemService(PRINT_SERVICE);PrintJob job=manager.print("Airitom chek "+eid,page.createPrintDocumentAdapter("Airitom chek"),null);
        Runnable poll=new Runnable(){public void run(){if(job.isCompleted())finish(id,eid,"android","confirmed","Android chop xizmati tasdiqladi");else if(job.isFailed()||job.isCancelled())finish(id,eid,"android",job.isCancelled()?"cancelled":"failed","Android chop xizmati");else if(active.equals(id))ui.postDelayed(this,1500);}};ui.postDelayed(poll,1500);
    }
    private void settings(){new AlertDialog.Builder(this).setTitle("Chek printeri").setItems(new String[]{"SUNMI V2s ichki printeri · 58 mm","Bluetooth ESC/POS printer","Wi-Fi / LAN ESC/POS printer","Android chop xizmati"},(d,which)->{
        if(which==0){preferences.edit().putString("method","internal").apply();bindPrinter();toast("SUNMI ichki printer tanlandi");}
        if(which==1)bluetooth();if(which==2)wifi();if(which==3){preferences.edit().putString("method","android").apply();toast("Android chop xizmati tanlandi");}
    }).show();}
    private void bluetooth(){
        if(Build.VERSION.SDK_INT>=31&&checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT},41);return;}
        BluetoothAdapter adapter=BluetoothAdapter.getDefaultAdapter();if(adapter==null||!adapter.isEnabled()){toast("Android sozlamasida Bluetoothni yoqing va printerni juftlang");return;}
        ArrayList<BluetoothDevice> devices=new ArrayList<>(adapter.getBondedDevices());String[] labels=new String[devices.size()];for(int i=0;i<labels.length;i++)labels[i]=devices.get(i).getName()+" · "+devices.get(i).getAddress();
        if(labels.length==0){toast("Avval Android sozlamasida printerni juftlang");return;}
        new AlertDialog.Builder(this).setTitle("Juftlangan printer").setItems(labels,(d,i)->{preferences.edit().putString("method","bluetooth").putString("address",devices.get(i).getAddress()).apply();paper();}).show();
    }
    private void wifi(){
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(24,12,24,12);
        EditText host=new EditText(this);host.setHint("Printer IP: 192.168.1.100");host.setSingleLine();host.setText(preferences.getString("host",""));form.addView(host);
        EditText port=new EditText(this);port.setInputType(2);port.setText(String.valueOf(preferences.getInt("port",9100)));form.addView(port);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Wi-Fi / LAN printer").setView(form).setPositiveButton("Saqlash",null).setNegativeButton("Bekor",null).create();dialog.setOnShowListener(d->dialog.getButton(-1).setOnClickListener(v->{try{int p=Integer.parseInt(port.getText().toString());String h=host.getText().toString().trim();if(p<1||p>65535||!h.matches("[a-zA-Z0-9.:-]{1,253}"))throw new Exception();preferences.edit().putString("method","wifi").putString("host",h).putInt("port",p).apply();dialog.dismiss();paper();}catch(Exception e){host.setError("IP manzil va portni tekshiring");}}));dialog.show();
    }
    private void paper(){new AlertDialog.Builder(this).setTitle("Tashqi printer qog‘ozi").setItems(new String[]{"58 mm","80 mm"},(d,i)->{preferences.edit().putInt("width",i==0?384:576).apply();toast("Printer saqlandi");}).show();}
    private void toast(String text){Toast.makeText(this,text,Toast.LENGTH_LONG).show();}
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){
        super.onRequestPermissionsResult(code,permissions,results);
        boolean granted=results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED;
        if(code==42 && pendingCamera!=null){
            if(granted && trustedPage() && trusted(pendingCamera.getOrigin()))pendingCamera.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else pendingCamera.deny();
            pendingCamera=null;
        }else if(code==41){if(granted)bluetooth();else toast("Bluetooth ruxsati berilmadi");}
    }
    @Override protected void onResume(){super.onResume();if(page!=null)ui.postDelayed(this::replay,600);}
    @Override protected void onDestroy(){try{InnerPrinterManager.getInstance().unBindService(this,connection);}catch(Exception ignored){}worker.shutdown();deadlines.shutdown();super.onDestroy();}
    @Override public void onBackPressed(){if(printing){toast("Chek chop etilishi tugashini kuting");return;}if(page.canGoBack())openStation();else super.onBackPressed();}
}

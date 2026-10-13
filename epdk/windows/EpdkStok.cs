// =============================================================================
//  EPDK Petrol Stok İzleme — Windows için kurulumsuz yerel sunucu
// =============================================================================
//
//  Python gerektirmeyen Windows sürümü. Windows 8 / Windows Server 2012 ve
//  sonrasında hazır gelen .NET Framework 4.5'in içindeki C# derleyicisi
//  (csc.exe) ile ilk açılışta derlenir: kurulum, yönetici yetkisi ya da
//  internetten dosya indirmek gerekmez. Bunu BASLAT.bat yapar.
//
//  Arayüz (epdk/web) ve /api sözleşmesi Python sürümüyle birebir aynıdır;
//  test paketi iki sunucuyu da aynı testlerle doğrular. Alan tanımları
//  epdk/web/schema.json dosyasından okunur (python -m epdk.export_schema).
//
//  Elle derlemek için:
//    csc /nologo /optimize+ /r:System.Web.Extensions.dll /out:EpdkStok.exe EpdkStok.cs
//
//  ÖNEMLİ: Windows Server 2012'deki csc.exe yalnızca C# 5'i tanır. Bu dosyada
//  $"..." , ?. , nameof, => gövdeli üyeler gibi yeni söz dizimleri bilerek
//  kullanılmamıştır. (Test paketi dosyayı -langversion:5 ile derleyerek bunu
//  denetler.)
// =============================================================================

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace EpdkStok
{
    using JObj = System.Collections.Generic.Dictionary<string, object>;
    using Query = System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<string>>;

    // ------------------------------------------------------------------ JSON
    static class Json
    {
        static JavaScriptSerializer Serializer()
        {
            var serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = int.MaxValue;
            serializer.RecursionLimit = 512;
            return serializer;
        }

        public static object Parse(string text)
        {
            return Serializer().DeserializeObject(text);
        }

        public static string Write(object value)
        {
            return Serializer().Serialize(value);
        }
    }

    // ------------------------------------------------------------- yardımcı
    static class U
    {
        public static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
        public static readonly Encoding Utf8 = new UTF8Encoding(false);
        static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        public static JObj Obj(params object[] pairs)
        {
            var result = new JObj();
            for (int i = 0; i + 1 < pairs.Length; i += 2) result[(string)pairs[i]] = pairs[i + 1];
            return result;
        }

        public static object Get(JObj source, string key)
        {
            object value;
            if (source != null && source.TryGetValue(key, out value)) return value;
            return null;
        }

        public static JObj AsObj(object value)
        {
            return value as JObj;
        }

        public static List<object> AsList(object value)
        {
            if (value == null || value is string || value is JObj) return null;
            var array = value as object[];
            if (array != null) return new List<object>(array);
            var list = value as List<object>;
            if (list != null) return list;
            var sequence = value as IEnumerable;
            if (sequence != null) return sequence.Cast<object>().ToList();
            return null;
        }

        public static bool IsNumeric(object value)
        {
            return value is int || value is long || value is decimal || value is double
                || value is float || value is short || value is byte;
        }

        /// <summary>Python str() eşdeğeri; sayılar kültürden bağımsız yazılır.</summary>
        public static string Str(object value)
        {
            if (value == null) return "";
            var text = value as string;
            if (text != null) return text;
            if (value is bool) return (bool)value ? "True" : "False";
            if (value is decimal) return ((decimal)value).ToString(Inv);
            if (value is double) return ((double)value).ToString("R", Inv);
            if (value is float) return ((float)value).ToString("R", Inv);
            var formattable = value as IFormattable;
            if (formattable != null) return formattable.ToString(null, Inv);
            return value.ToString();
        }

        /// <summary>Python'daki <c>str(value or "")</c>: boş, 0 ve False boş metin olur.</summary>
        public static string StrOr(object value)
        {
            return Truthy(value) ? Str(value) : "";
        }

        /// <summary>Python bool() eşdeğeri.</summary>
        public static bool Truthy(object value)
        {
            if (value == null) return false;
            if (value is bool) return (bool)value;
            var text = value as string;
            if (text != null) return text.Length > 0;
            if (IsNumeric(value))
            {
                try { return Convert.ToDecimal(value, Inv) != 0m; }
                catch (Exception) { return true; }
            }
            var map = value as JObj;
            if (map != null) return map.Count > 0;
            var list = AsList(value);
            if (list != null) return list.Count > 0;
            return true;
        }

        public static long? ToLongOrNull(object value)
        {
            if (value == null) return null;
            try { return Convert.ToInt64(Convert.ToDecimal(value, Inv)); }
            catch (Exception) { return null; }
        }

        public static double UnixNow()
        {
            return (DateTime.UtcNow - Epoch).TotalSeconds;
        }

        public static string IsoNow()
        {
            return DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss", Inv);
        }

        public static string Clip(string text, int length)
        {
            if (text == null) return null;
            return text.Length > length ? text.Substring(0, length) : text;
        }
    }

    // ------------------------------------------------------------ hatalar
    sealed class EpdkException : Exception
    {
        public int? Status;
        public object Payload;

        public EpdkException(string message, int? status = null, object payload = null)
            : base(message)
        {
            Status = status;
            Payload = payload;
        }
    }

    sealed class ApiException : Exception
    {
        public int Status;
        public object Details;

        public ApiException(string message, int status = 400, object details = null)
            : base(message)
        {
            Status = status;
            Details = details;
        }
    }

    // ============================================================= ayarlar
    /// <summary>
    /// Ayarlar Python sürümüyle aynı dosyada tutulur (~/.epdk-stok/settings.json),
    /// böylece iki sürüm arasında geçişte kayıtlı müşteriler kaybolmaz.
    /// Parola hiçbir zaman diske yazılmaz.
    /// </summary>
    static class Settings
    {
        static readonly object Gate = new object();
        public const int MaxProfiles = 20;

        public static JObj Defaults()
        {
            return U.Obj(
                "environment", "test",
                "custom_base_url", "",
                "username", "",
                "profiles", new List<object>(),
                "language", "tr",
                "theme", "light",
                "timeout", 45,
                "auto_renew", true,
                "confirm_delete", true,
                "log_limit", 2000,
                "gumruk_labels", U.Obj(
                    "0", "Millileşmiş (serbest dolaşımda)",
                    "1", "Gümrüklü (antrepo)"));
        }

        public static string Home()
        {
            string over = Environment.GetEnvironmentVariable("EPDK_APP_HOME");
            string home = string.IsNullOrEmpty(over)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".epdk-stok")
                : over;
            Directory.CreateDirectory(home);
            return home;
        }

        static string FilePath()
        {
            return Path.Combine(Home(), "settings.json");
        }

        public static JObj Load()
        {
            lock (Gate)
            {
                var data = Defaults();
                string path = FilePath();
                if (!File.Exists(path)) return data;
                try
                {
                    var stored = Json.Parse(File.ReadAllText(path, Encoding.UTF8)) as JObj;
                    if (stored != null)
                    {
                        foreach (var pair in stored)
                        {
                            if (pair.Key != "password" && data.ContainsKey(pair.Key)) data[pair.Key] = pair.Value;
                        }
                    }
                }
                catch (Exception)
                {
                    // Bozuk ayar dosyası varsayılanlarla açılır.
                }
                return data;
            }
        }

        public static JObj Save(JObj values)
        {
            lock (Gate)
            {
                var current = Load();
                if (values != null)
                {
                    foreach (var pair in values)
                    {
                        if (pair.Key != "password" && current.ContainsKey(pair.Key)) current[pair.Key] = pair.Value;
                    }
                }
                current.Remove("password");
                // BOM'suz UTF-8: Python sürümü de aynı dosyayı okuyabilmeli.
                File.WriteAllText(FilePath(), Json.Write(current), U.Utf8);
                return current;
            }
        }

        public static void UpsertProfile(JObj settings, string username, string environment, string customUrl)
        {
            var profiles = (U.AsList(U.Get(settings, "profiles")) ?? new List<object>())
                .OfType<JObj>()
                .Where(p => !(U.Str(U.Get(p, "username")) == username
                              && U.Str(U.Get(p, "environment")) == environment))
                .Cast<object>()
                .ToList();

            profiles.Insert(0, U.Obj(
                "username", username,
                "environment", environment,
                "custom_base_url", environment == "custom" ? customUrl : "",
                "licence", username.StartsWith("WSU-") ? username.Substring(4) : username,
                "last_used", U.IsoNow()));

            settings["profiles"] = profiles.Take(MaxProfiles).ToList();
        }

        public static void RemoveProfile(JObj settings, string username, string environment)
        {
            settings["profiles"] = (U.AsList(U.Get(settings, "profiles")) ?? new List<object>())
                .OfType<JObj>()
                .Where(p => !(U.Str(U.Get(p, "username")) == username
                              && U.Str(U.Get(p, "environment")) == environment))
                .Cast<object>()
                .ToList();
        }

        public static string ResolveBaseUrl(JObj settings, JObj environments)
        {
            string environment = U.Str(U.Get(settings, "environment"));
            if (environment == "custom") return U.Str(U.Get(settings, "custom_base_url")).TrimEnd('/');
            var entry = U.AsObj(U.Get(environments, environment)) ?? U.AsObj(U.Get(environments, "test"));
            return U.Str(U.Get(entry, "base_url")).TrimEnd('/');
        }
    }

    // ===================================================== işlem geçmişi
    /// <summary>
    /// Uygulamadan yapılan her servis çağrısının kaydı (JSON satırları).
    /// Windows'ta hazır bir SQLite olmadığından düz dosya kullanılır.
    /// </summary>
    sealed class ActivityLog
    {
        const int MaxPayloadChars = 20000;
        readonly object _gate = new object();
        readonly string _path;
        long _nextId = 1;
        int _lines;
        public int Limit;

        public ActivityLog(string path, int limit)
        {
            _path = path;
            Limit = Math.Max(100, limit);
            foreach (var entry in ReadAll())
            {
                long id = U.ToLongOrNull(U.Get(entry, "id")) ?? 0;
                if (id >= _nextId) _nextId = id + 1;
                _lines++;
            }
        }

        List<JObj> ReadAll()
        {
            var result = new List<JObj>();
            if (!File.Exists(_path)) return result;
            foreach (var line in File.ReadAllLines(_path, Encoding.UTF8))
            {
                if (line.Trim().Length == 0) continue;
                try
                {
                    var entry = Json.Parse(line) as JObj;
                    if (entry != null) result.Add(entry);
                }
                catch (Exception)
                {
                    // Yarım yazılmış satır atlanır.
                }
            }
            return result;
        }

        public void Record(string action, bool success, string environment, string username,
                           string tableKey, string endpoint, int? status, string message,
                           string recordId, object request, object response, int? durationMs)
        {
            lock (_gate)
            {
                var entry = U.Obj(
                    "id", _nextId++,
                    "ts", U.IsoNow(),
                    "environment", environment ?? "",
                    "username", username ?? "",
                    "table_key", tableKey ?? "",
                    "action", action,
                    "endpoint", endpoint ?? "",
                    "success", success,
                    "status", status,
                    "message", U.Clip(message ?? "", 2000),
                    "record_id", recordId,
                    "request", Fit(MaskSecrets(request)),
                    "response", Fit(response),
                    "duration_ms", durationMs);
                File.AppendAllText(_path, Json.Write(entry) + "\n", U.Utf8);
                _lines++;
                if (_lines > Limit + Math.Max(50, Limit / 4)) Trim();
            }
        }

        void Trim()
        {
            var all = ReadAll();
            var keep = all.Skip(Math.Max(0, all.Count - Limit)).Select(e => Json.Write(e)).ToArray();
            File.WriteAllLines(_path, keep, U.Utf8);
            _lines = keep.Length;
        }

        static object Fit(object value)
        {
            if (value == null) return null;
            string text;
            try { text = Json.Write(value); }
            catch (Exception) { return U.Clip(U.Str(value), MaxPayloadChars); }
            return text.Length <= MaxPayloadChars ? value : text.Substring(0, MaxPayloadChars);
        }

        /// <summary>Parola gibi alanların kayıt defterine yazılmasını engeller.</summary>
        public static object MaskSecrets(object value)
        {
            var map = value as JObj;
            if (map != null)
            {
                var masked = new JObj();
                foreach (var pair in map)
                {
                    string key = pair.Key.ToLowerInvariant();
                    masked[pair.Key] = (key == "password" || key == "sifre" || key == "parola")
                        ? (object)"••••••"
                        : MaskSecrets(pair.Value);
                }
                return masked;
            }
            var list = U.AsList(value);
            if (list != null) return list.Select(MaskSecrets).ToList();
            return value;
        }

        public List<JObj> List(int limit, string tableKey, bool onlyFailures, string search)
        {
            limit = Math.Max(1, Math.Min(limit, 5000));
            List<JObj> all;
            lock (_gate) { all = ReadAll(); }

            IEnumerable<JObj> query = all;
            if (!string.IsNullOrEmpty(tableKey))
                query = query.Where(e => U.Str(U.Get(e, "table_key")) == tableKey);
            if (onlyFailures)
                query = query.Where(e => !U.Truthy(U.Get(e, "success")));
            if (!string.IsNullOrEmpty(search))
                query = query.Where(e => Contains(U.Str(U.Get(e, "message")), search)
                                      || Contains(PayloadText(U.Get(e, "request")), search)
                                      || Contains(U.Str(U.Get(e, "action")), search));
            return query.Reverse().Take(limit).ToList();
        }

        static string PayloadText(object value)
        {
            if (value == null) return "";
            var text = value as string;
            return text ?? Json.Write(value);
        }

        static bool Contains(string text, string needle)
        {
            return text.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        public JObj Stats()
        {
            List<JObj> all;
            lock (_gate) { all = ReadAll(); }
            string today = DateTime.Now.ToString("yyyy-MM-dd", U.Inv);
            return U.Obj(
                "total", all.Count,
                "failures", all.Count(e => !U.Truthy(U.Get(e, "success"))),
                "today", all.Count(e => U.Str(U.Get(e, "ts")).StartsWith(today)));
        }

        public void Clear()
        {
            lock (_gate)
            {
                if (File.Exists(_path)) File.Delete(_path);
                _lines = 0;
            }
        }
    }

    // ===================================================== EPDK istemcisi
    /// <summary>
    /// Küçük, bağımsız bir HTTP/HTTPS istemcisi.
    ///
    /// EPDK'nın sorgu metotları GET olmasına rağmen gövdede <c>{"kullanici":…}</c>
    /// bekler. .NET Framework'ün HttpWebRequest/HttpClient sınıfları GET ile gövde
    /// göndermeyi reddettiği için istek doğrudan soket üzerinden yazılır.
    /// TLS 1.2 açıkça istenir; sistem proxy ayarı (Internet Seçenekleri) izlenir.
    /// </summary>
    static class RawHttp
    {
        public sealed class Reply
        {
            public int Status;
            public string Body;
        }

        [ThreadStatic] static string _certificateProblem;

        public static string LastCertificateProblem()
        {
            return _certificateProblem;
        }

        public static Reply Send(string method, Uri uri, byte[] body,
                                 IDictionary<string, string> headers, int timeoutMs)
        {
            TcpClient tcp = null;
            Stream stream = null;
            try
            {
                bool https = string.Equals(uri.Scheme, "https", StringComparison.OrdinalIgnoreCase);
                Uri proxy = ResolveProxy(uri);

                if (proxy != null)
                {
                    tcp = Connect(proxy.Host, proxy.Port, timeoutMs);
                    stream = tcp.GetStream();
                    if (https) Tunnel(stream, uri.Host, uri.Port);
                }
                else
                {
                    tcp = Connect(uri.Host, uri.Port, timeoutMs);
                    stream = tcp.GetStream();
                }

                if (https)
                {
                    _certificateProblem = null;
                    var ssl = new SslStream(stream, false, ValidateCertificate);
                    ssl.ReadTimeout = timeoutMs;
                    ssl.WriteTimeout = timeoutMs;
                    // Sertifika zinciri ve ad doğrulanır; iptal listesi denetimi
                    // (CRL) internete kapalı sunucularda yanlış hataya yol açtığından
                    // Python sürümündeki gibi yapılmaz.
                    ssl.AuthenticateAsClient(uri.Host, null,
                        SslProtocols.Tls12 | SslProtocols.Tls11 | SslProtocols.Tls, false);
                    stream = ssl;
                }

                string target = (proxy != null && !https) ? uri.AbsoluteUri : uri.PathAndQuery;
                var head = new StringBuilder();
                head.Append(method).Append(' ').Append(target).Append(" HTTP/1.1\r\n");
                head.Append("Host: ").Append(uri.IsDefaultPort ? uri.Host : uri.Host + ":" + uri.Port.ToString(U.Inv)).Append("\r\n");
                foreach (var header in headers) head.Append(header.Key).Append(": ").Append(header.Value).Append("\r\n");
                head.Append("Content-Length: ").Append(body.Length.ToString(U.Inv)).Append("\r\n");
                head.Append("Connection: close\r\n\r\n");

                byte[] headBytes = Encoding.UTF8.GetBytes(head.ToString());
                stream.Write(headBytes, 0, headBytes.Length);
                if (body.Length > 0) stream.Write(body, 0, body.Length);
                stream.Flush();

                return ReadReply(stream);
            }
            finally
            {
                if (stream != null) { try { stream.Dispose(); } catch (Exception) { } }
                if (tcp != null) { try { tcp.Close(); } catch (Exception) { } }
            }
        }

        static bool ValidateCertificate(object sender, X509Certificate certificate, X509Chain chain,
                                        SslPolicyErrors errors)
        {
            if (errors == SslPolicyErrors.None) return true;
            var detail = new StringBuilder(errors.ToString());
            if (chain != null)
            {
                foreach (var status in chain.ChainStatus)
                {
                    detail.Append("; ").Append((status.StatusInformation ?? "").Trim());
                }
            }
            _certificateProblem = detail.ToString();
            return false;
        }

        static TcpClient Connect(string host, int port, int timeoutMs)
        {
            var client = new TcpClient();
            try
            {
                IAsyncResult pending = client.BeginConnect(host, port, null, null);
                if (!pending.AsyncWaitHandle.WaitOne(timeoutMs)) throw new TimeoutException();
                client.EndConnect(pending);
                client.NoDelay = true;
                client.ReceiveTimeout = timeoutMs;
                client.SendTimeout = timeoutMs;
                return client;
            }
            catch
            {
                client.Close();
                throw;
            }
        }

        static bool IsLoopback(string host)
        {
            if (string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase)) return true;
            IPAddress address;
            return IPAddress.TryParse(host, out address) && IPAddress.IsLoopback(address);
        }

        /// <summary>
        /// Windows'un sistem proxy ayarını (Internet Seçenekleri / WPAD) izler.
        /// EPDK_PROXY ortam değişkeni ile değiştirilebilir: "none" ya da adres.
        /// </summary>
        static Uri ResolveProxy(Uri target)
        {
            if (IsLoopback(target.Host)) return null;
            string configured = Environment.GetEnvironmentVariable("EPDK_PROXY");
            if (!string.IsNullOrEmpty(configured))
            {
                if (configured == "none" || configured == "direct") return null;
                Uri explicitProxy;
                return Uri.TryCreate(configured, UriKind.Absolute, out explicitProxy) ? explicitProxy : null;
            }
            try
            {
                IWebProxy system = WebRequest.GetSystemWebProxy();
                if (system == null || system.IsBypassed(target)) return null;
                Uri proxy = system.GetProxy(target);
                if (proxy == null || proxy == target || proxy.Host == target.Host) return null;
                return proxy;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>HTTPS için proxy üzerinden CONNECT tüneli açar.</summary>
        static void Tunnel(Stream stream, string host, int port)
        {
            string target = host + ":" + port.ToString(U.Inv);
            byte[] request = Encoding.ASCII.GetBytes(
                "CONNECT " + target + " HTTP/1.1\r\nHost: " + target + "\r\n\r\n");
            stream.Write(request, 0, request.Length);
            stream.Flush();

            // Başlıklar bitene dek bayt bayt okunur — TLS verisine taşılmaz.
            var answer = new StringBuilder();
            while (true)
            {
                int value = stream.ReadByte();
                if (value < 0) throw new IOException("Proxy bağlantıyı kapattı.");
                answer.Append((char)value);
                int n = answer.Length;
                if (n > 16384) throw new IOException("Proxy yanıtı çözümlenemedi.");
                if (n >= 4 && answer[n - 4] == '\r' && answer[n - 3] == '\n'
                    && answer[n - 2] == '\r' && answer[n - 1] == '\n') break;
            }

            string text = answer.ToString();
            string first = text.Substring(0, Math.Max(0, text.IndexOf('\r')));
            string[] parts = first.Split(' ');
            int code;
            if (parts.Length < 2 || !int.TryParse(parts[1], NumberStyles.Integer, U.Inv, out code)) code = 0;
            if (code == 407)
                throw new EpdkException("Proxy sunucusu kimlik doğrulaması istiyor (407). "
                    + "Bu sürüm kimlik doğrulamalı proxy'yi desteklemez; BT biriminden "
                    + "petrolstok.epdk.gov.tr için proxy istisnası isteyin.");
            if (code != 200) throw new EpdkException("Proxy üzerinden bağlantı kurulamadı: " + first);
        }

        sealed class ByteReader
        {
            readonly Stream _stream;
            readonly byte[] _buffer = new byte[8192];
            int _position, _length;

            public ByteReader(Stream stream)
            {
                _stream = stream;
            }

            bool Fill()
            {
                _length = _stream.Read(_buffer, 0, _buffer.Length);
                _position = 0;
                return _length > 0;
            }

            public int ReadByte()
            {
                if (_position >= _length && !Fill()) return -1;
                return _buffer[_position++];
            }

            public string ReadLine()
            {
                var bytes = new List<byte>();
                while (true)
                {
                    int value = ReadByte();
                    if (value < 0)
                    {
                        if (bytes.Count == 0) return null;
                        break;
                    }
                    if (value == '\n') break;
                    bytes.Add((byte)value);
                    if (bytes.Count > 65536) throw new IOException("HTTP başlığı çok uzun.");
                }
                if (bytes.Count > 0 && bytes[bytes.Count - 1] == '\r') bytes.RemoveAt(bytes.Count - 1);
                return Encoding.UTF8.GetString(bytes.ToArray());
            }

            public byte[] ReadExact(long count)
            {
                var output = new MemoryStream();
                while (count > 0)
                {
                    if (_position >= _length && !Fill()) break;
                    int take = (int)Math.Min(count, _length - _position);
                    output.Write(_buffer, _position, take);
                    _position += take;
                    count -= take;
                }
                return output.ToArray();
            }

            public byte[] ReadToEnd()
            {
                var output = new MemoryStream();
                if (_position < _length)
                {
                    output.Write(_buffer, _position, _length - _position);
                    _position = _length;
                }
                try
                {
                    int read;
                    while ((read = _stream.Read(_buffer, 0, _buffer.Length)) > 0) output.Write(_buffer, 0, read);
                }
                catch (IOException)
                {
                    // Sunucu bağlantıyı TLS kapanışı göndermeden kesti; okunan kadarı yeterli.
                }
                return output.ToArray();
            }

            public byte[] ReadChunked()
            {
                var output = new MemoryStream();
                while (true)
                {
                    string sizeLine = ReadLine();
                    if (sizeLine == null) break;
                    int semicolon = sizeLine.IndexOf(';');
                    if (semicolon >= 0) sizeLine = sizeLine.Substring(0, semicolon);
                    sizeLine = sizeLine.Trim();
                    if (sizeLine.Length == 0) continue;
                    long size = long.Parse(sizeLine, NumberStyles.HexNumber, U.Inv);
                    if (size == 0)
                    {
                        while (!string.IsNullOrEmpty(ReadLine())) { }
                        break;
                    }
                    byte[] chunk = ReadExact(size);
                    output.Write(chunk, 0, chunk.Length);
                    ReadLine();
                }
                return output.ToArray();
            }
        }

        static Reply ReadReply(Stream stream)
        {
            var reader = new ByteReader(stream);
            while (true)
            {
                string statusLine = reader.ReadLine();
                if (statusLine == null) throw new IOException("Sunucu yanıt vermeden bağlantıyı kapattı.");
                string[] parts = statusLine.Split(' ');
                int status;
                if (parts.Length < 2 || !int.TryParse(parts[1], NumberStyles.Integer, U.Inv, out status))
                    throw new IOException("Geçersiz HTTP yanıtı: " + statusLine);

                var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                string line;
                while (!string.IsNullOrEmpty(line = reader.ReadLine()))
                {
                    int colon = line.IndexOf(':');
                    if (colon > 0) headers[line.Substring(0, colon).Trim()] = line.Substring(colon + 1).Trim();
                }
                if (status >= 100 && status < 200) continue;

                byte[] body;
                string value;
                if (headers.TryGetValue("Transfer-Encoding", out value)
                    && value.IndexOf("chunked", StringComparison.OrdinalIgnoreCase) >= 0)
                    body = reader.ReadChunked();
                else if (headers.TryGetValue("Content-Length", out value))
                    body = reader.ReadExact(long.Parse(value, U.Inv));
                else
                    body = reader.ReadToEnd();

                if (headers.TryGetValue("Content-Encoding", out value)
                    && value.IndexOf("gzip", StringComparison.OrdinalIgnoreCase) >= 0)
                    body = Gunzip(body);

                string text = Encoding.UTF8.GetString(body);
                if (text.Length > 0 && text[0] == '﻿') text = text.Substring(1);
                return new Reply { Status = status, Body = text };
            }
        }

        static byte[] Gunzip(byte[] data)
        {
            using (var input = new MemoryStream(data))
            using (var gzip = new GZipStream(input, CompressionMode.Decompress))
            using (var output = new MemoryStream())
            {
                gzip.CopyTo(output);
                return output.ToArray();
            }
        }
    }

    sealed class ApiResult
    {
        public bool Success;
        public object Message;
        public object Data;
        public int Status;
        public object Raw;
        public int DurationMs;

        public JObj AsDict()
        {
            return U.Obj("success", Success, "message", Message, "data", Data,
                         "status", Status, "durationMs", DurationMs);
        }
    }

    /// <summary>
    /// EPDK web servis sözleşmesi (kılavuz Bölüm 2–3): yanıtlar
    /// <c>{success, message, data}</c>; login başarılıysa token <c>message</c> içinde.
    /// </summary>
    sealed class EpdkClient
    {
        const string UserAgent = "EPDK-Stok-Masaustu/1.0 (.NET)";
        readonly string _baseUrl;
        readonly int _timeout;

        public EpdkClient(string baseUrl, int timeoutSeconds)
        {
            _baseUrl = baseUrl.TrimEnd('/');
            _timeout = timeoutSeconds;
        }

        static bool IsTimeout(Exception error)
        {
            for (var current = error; current != null; current = current.InnerException)
            {
                if (current is TimeoutException) return true;
                var socket = current as SocketException;
                if (socket != null && socket.SocketErrorCode == SocketError.TimedOut) return true;
            }
            return false;
        }

        static string Reason(Exception error)
        {
            var current = error;
            while (current.InnerException != null) current = current.InnerException;
            return current.Message;
        }

        public ApiResult Request(string path, string method, JObj body, string token)
        {
            string url = _baseUrl + "/" + path.Trim('/');
            Uri uri;
            if (!Uri.TryCreate(url, UriKind.Absolute, out uri))
                throw new EpdkException("Geçersiz servis adresi: " + url);

            byte[] payload = Encoding.UTF8.GetBytes(Json.Write(body ?? new JObj()));
            var headers = new Dictionary<string, string>();
            headers["Content-Type"] = "application/json; charset=utf-8";
            headers["Accept"] = "application/json";
            headers["User-Agent"] = UserAgent;
            if (!string.IsNullOrEmpty(token)) headers["Authorization"] = "Bearer " + token;

            var watch = Stopwatch.StartNew();
            RawHttp.Reply reply;
            try
            {
                reply = RawHttp.Send(method, uri, payload, headers, _timeout * 1000);
            }
            catch (EpdkException)
            {
                throw;
            }
            catch (AuthenticationException error)
            {
                string problem = RawHttp.LastCertificateProblem();
                if (!string.IsNullOrEmpty(problem))
                    throw new EpdkException("Sunucu sertifikası doğrulanamadı (" + problem + "). "
                        + "Windows'un kök sertifikaları güncel olmayabilir; Windows Update ile "
                        + "kök sertifika güncellemelerini yükleyin.");
                throw new EpdkException("Güvenli bağlantı (TLS 1.2) kurulamadı: " + Reason(error)
                    + ". Eski Windows sürümlerinde TLS 1.2 şifreleme desteği eksik olabilir; "
                    + "bu durumda Python sürümünü kullanın.");
            }
            catch (Exception error)
            {
                if (IsTimeout(error))
                    throw new EpdkException(string.Format(U.Inv,
                        "Servis {0} saniye içinde yanıt vermedi ({1}).", _timeout, url));
                if (error is IOException || error is SocketException || error is ObjectDisposedException)
                    throw new EpdkException("Servise ulaşılamadı: " + Reason(error)
                        + ". Adresi ve internet bağlantınızı kontrol edin (" + url + ").");
                throw;
            }
            int duration = (int)watch.ElapsedMilliseconds;

            string text = reply.Body ?? "";
            object parsed;
            if (text.Trim().Length == 0)
            {
                parsed = new JObj();
            }
            else
            {
                try
                {
                    parsed = Json.Parse(text);
                }
                catch (Exception)
                {
                    // Servis bazı hata durumlarında düz metin/HTML döndürebilir.
                    if (reply.Status >= 400)
                        throw new EpdkException(string.Format(U.Inv, "Servis {0} kodu ile yanıt verdi.", reply.Status),
                            reply.Status, U.Obj("body", U.Clip(text, 1000)));
                    parsed = U.Obj("success", true, "message", text.Trim(), "data", null);
                }
            }

            var array = parsed as object[];
            if (array != null) parsed = U.Obj("success", reply.Status < 400, "message", null, "data", array);
            var map = parsed as JObj;
            if (map == null) map = U.Obj("success", reply.Status < 400, "message", U.Str(parsed), "data", null);

            bool success = map.ContainsKey("success") ? U.Truthy(map["success"]) : reply.Status < 400;
            return new ApiResult
            {
                Success = success,
                Message = U.Get(map, "message"),
                Data = U.Get(map, "data"),
                Status = reply.Status,
                Raw = map,
                DurationMs = duration,
            };
        }

        public JObj Login(string username, string password)
        {
            var result = Request("authentication/login", "POST",
                U.Obj("username", username, "password", password), null);
            if (!result.Success)
                throw new EpdkException(U.Truthy(result.Message) ? U.Str(result.Message) : "Oturum açılamadı.",
                    result.Status);

            string token = ExtractToken(result);
            if (token.Length == 0)
                throw new EpdkException("Servis oturum açma yanıtında token bulunamadı.", result.Status, result.Raw);

            var claims = DecodeToken(token);
            object userName = U.Truthy(U.Get(claims, "userName")) ? U.Get(claims, "userName") : username;
            return U.Obj(
                "token", token,
                "claims", claims,
                "expiresAt", U.Get(claims, "exp"),
                "licence", U.Get(claims, "licenceNumber"),
                "userName", userName,
                "durationMs", result.DurationMs);
        }

        /// <summary>Token'ı yanıtın bilinen tüm konumlarında arar (kılavuz: message alanı).</summary>
        static string ExtractToken(ApiResult result)
        {
            var candidates = new List<object> { result.Message };
            if (result.Data is string)
            {
                candidates.Add(result.Data);
            }
            else
            {
                var data = result.Data as JObj;
                if (data != null)
                    foreach (var key in new[] { "token", "accessToken", "jwt", "message" }) candidates.Add(U.Get(data, key));
            }
            var raw = result.Raw as JObj;
            if (raw != null)
                foreach (var key in new[] { "token", "accessToken", "jwt" }) candidates.Add(U.Get(raw, key));

            foreach (var candidate in candidates)
            {
                var text = candidate as string;
                if (text == null) continue;
                text = text.Trim();
                if (text.Count(c => c == '.') == 2 && text.Length > 40) return text;
            }
            return "";
        }

        /// <summary>JWT gövdesini çözer (imza doğrulanmaz — yalnızca bilgi amaçlı).</summary>
        public static JObj DecodeToken(string token)
        {
            try
            {
                string part = token.Split('.')[1].Replace('-', '+').Replace('_', '/');
                switch (part.Length % 4)
                {
                    case 2: part += "=="; break;
                    case 3: part += "="; break;
                }
                return Json.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(part))) as JObj ?? new JObj();
            }
            catch (Exception)
            {
                return new JObj();
            }
        }

        public ApiResult Query(string path, string username, string token)
        {
            return Request(path, "GET", U.Obj("kullanici", username), token);
        }

        public ApiResult Save(string tablePath, JObj payload, string token)
        {
            return Request(tablePath + "/save", "POST", payload, token);
        }

        public ApiResult Update(string tablePath, JObj payload, string token)
        {
            return Request(tablePath + "/update", "POST", payload, token);
        }

        public ApiResult Delete(string tablePath, string recordId, string username, string token)
        {
            return Request(tablePath + "/delete", "POST", U.Obj("id", recordId, "kullanici", username), token);
        }
    }

    // ============================================================ oturum
    /// <summary>Açık EPDK oturumu. Parola yalnızca otomatik yenileme için ve yalnızca bellekte.</summary>
    sealed class Session
    {
        const int RenewMarginSeconds = 300;
        const int CacheSeconds = 600;
        readonly object _gate = new object();
        readonly Dictionary<string, KeyValuePair<double, object>> _cache =
            new Dictionary<string, KeyValuePair<double, object>>();

        public string Token = "";
        public string Username = "";
        public string BaseUrl = "";
        public string EnvironmentKey = "";
        public JObj Claims = new JObj();
        public long? ExpiresAt;
        public int Timeout = 45;
        public double? LoggedInAt;
        string _password = "";
        bool _autoRenew;

        public bool Active
        {
            get { return Token.Length > 0; }
        }

        public List<object> Licences()
        {
            return U.StrOr(U.Get(Claims, "licenceNumber")).Split(',')
                .Select(p => p.Trim()).Where(p => p.Length > 0).Cast<object>().ToList();
        }

        public long? SecondsLeft()
        {
            if (!ExpiresAt.HasValue || ExpiresAt.Value == 0) return null;
            return Math.Max(0L, (long)(ExpiresAt.Value - U.UnixNow()));
        }

        public EpdkClient Client()
        {
            return new EpdkClient(BaseUrl, Timeout);
        }

        public JObj Login(string baseUrl, string environment, string username, string password,
                          int timeout, bool autoRenew)
        {
            var info = new EpdkClient(baseUrl, timeout).Login(username, password);
            lock (_gate)
            {
                Token = (string)info["token"];
                Username = username;
                Claims = U.AsObj(info["claims"]) ?? new JObj();
                ExpiresAt = U.ToLongOrNull(info["expiresAt"]);
                BaseUrl = baseUrl.TrimEnd('/');
                EnvironmentKey = environment;
                Timeout = timeout;
                LoggedInAt = U.UnixNow();
                _autoRenew = autoRenew;
                _password = autoRenew ? password : "";
                // Farklı müşteriye geçilmiş olabilir: önceki lisansın listeleri silinir.
                _cache.Clear();
            }
            return Describe();
        }

        public void Logout()
        {
            lock (_gate)
            {
                Token = "";
                Claims = new JObj();
                ExpiresAt = null;
                _password = "";
                LoggedInAt = null;
                _cache.Clear();
            }
        }

        /// <summary>Geçerli token'ı döndürür; süresi dolmak üzereyse yeniler.</summary>
        public string EnsureToken()
        {
            lock (_gate)
            {
                if (Token.Length == 0) throw new EpdkException("Oturum açık değil. Lütfen giriş yapın.");
                long? remaining = SecondsLeft();
                bool needsRenew = remaining.HasValue && remaining.Value <= RenewMarginSeconds;
                bool canRenew = _autoRenew && _password.Length > 0;

                if (needsRenew && canRenew)
                {
                    var info = new EpdkClient(BaseUrl, Timeout).Login(Username, _password);
                    Token = (string)info["token"];
                    Claims = U.AsObj(info["claims"]) ?? new JObj();
                    ExpiresAt = U.ToLongOrNull(info["expiresAt"]);
                }
                else if (remaining.HasValue && remaining.Value == 0)
                {
                    throw new EpdkException("Oturum süresi doldu (EPDK token ömrü 60 dakikadır). "
                        + "Lütfen yeniden giriş yapın.");
                }
                return Token;
            }
        }

        public object Cached(string key)
        {
            lock (_gate)
            {
                KeyValuePair<double, object> entry;
                if (!_cache.TryGetValue(key, out entry)) return null;
                if (U.UnixNow() - entry.Key > CacheSeconds) return null;
                return entry.Value;
            }
        }

        public void Cache(string key, object value)
        {
            lock (_gate) { _cache[key] = new KeyValuePair<double, object>(U.UnixNow(), value); }
        }

        public JObj Describe()
        {
            lock (_gate)
            {
                return U.Obj(
                    "active", Active,
                    "username", Username,
                    "environment", EnvironmentKey,
                    "baseUrl", BaseUrl,
                    "licences", Licences(),
                    "authority", U.Get(Claims, "authority"),
                    "expiresAt", ExpiresAt,
                    "secondsLeft", SecondsLeft(),
                    "autoRenew", _autoRenew && _password.Length > 0,
                    "loggedInAt", LoggedInAt);
            }
        }
    }

    // ============================================================= şema
    sealed class Schema
    {
        public JObj Raw;
        public JObj Environments;
        public List<JObj> Tables;

        public static Schema Load(string webRoot)
        {
            string path = Path.Combine(webRoot, "schema.json");
            var raw = Json.Parse(File.ReadAllText(path, Encoding.UTF8)) as JObj;
            if (raw == null) throw new InvalidDataException("schema.json okunamadı: " + path);
            return new Schema
            {
                Raw = raw,
                Environments = U.AsObj(U.Get(raw, "environments")) ?? new JObj(),
                Tables = (U.AsList(U.Get(raw, "tables")) ?? new List<object>()).OfType<JObj>().ToList(),
            };
        }

        public JObj Table(string key)
        {
            return Tables.FirstOrDefault(t => U.Str(U.Get(t, "key")) == key);
        }

        public JObj Describe()
        {
            return U.Obj("tables", U.Get(Raw, "tables"),
                         "readonlyColumns", U.Get(Raw, "readonlyColumns"),
                         "gumrukOptions", U.Get(Raw, "gumrukOptions"));
        }
    }

    // ======================================================== doğrulama
    /// <summary>
    /// Gönderim öncesi yerel denetim — epdk/validation.py ile aynı kurallar.
    /// "error" gönderimi engeller, "warning" yalnızca uyarır.
    /// </summary>
    static class Validator
    {
        static readonly Regex DateRe = new Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$");
        static readonly Regex DateTimeRe = new Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2})?$");
        static readonly Regex MinutePrecision = new Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$");

        static JObj Issue(string field, string tr, string en, string level = "error")
        {
            return U.Obj("field", field, "level", level, "message_tr", tr, "message_en", en);
        }

        static IEnumerable<JObj> Fields(JObj table)
        {
            return (U.AsList(U.Get(table, "fields")) ?? new List<object>()).OfType<JObj>();
        }

        static string F(JObj field, string key)
        {
            return U.Str(U.Get(field, key));
        }

        public static decimal? ToDecimal(object value)
        {
            if (value == null || value is bool) return null;
            if (value is decimal) return (decimal)value;
            if (value is int) return (int)value;
            if (value is long) return (long)value;
            if (value is double || value is float)
            {
                double number = Convert.ToDouble(value, U.Inv);
                if (double.IsNaN(number) || double.IsInfinity(number)) return null;
                return decimal.Parse(number.ToString("R", U.Inv), NumberStyles.Float, U.Inv);
            }
            string text = U.Str(value).Trim();
            if (text.Length == 0) return null;
            // 1.234,56 → binlik ayıracı nokta kabul edilir
            if (text.Contains(",") && text.Contains(".")) text = text.Replace(".", "").Replace(",", ".");
            else text = text.Replace(",", ".");
            decimal parsed;
            if (decimal.TryParse(text, NumberStyles.Float, U.Inv, out parsed)) return parsed;
            return null;
        }

        static string Trimmed(decimal value)
        {
            string text = value.ToString(U.Inv);
            if (text.Contains(".")) text = text.TrimEnd('0').TrimEnd('.');
            return text;
        }

        public static int DecimalPlaces(decimal value)
        {
            string text = Trimmed(value);
            int dot = text.IndexOf('.');
            return dot < 0 ? 0 : text.Length - dot - 1;
        }

        public static decimal Normalize(decimal value)
        {
            return decimal.Parse(Trimmed(value), NumberStyles.Float, U.Inv);
        }

        public static string NormalizeDateTime(object value)
        {
            string text = U.StrOr(value).Trim().Replace(" ", "T");
            if (MinutePrecision.IsMatch(text)) text += ":00";
            return text;
        }

        static bool HasLowercase(string text)
        {
            return text.Any(c => char.GetUnicodeCategory(c) == UnicodeCategory.LowercaseLetter);
        }

        static bool IsEmpty(object raw)
        {
            if (raw == null) return true;
            var text = raw as string;
            return text != null && text.Trim().Length == 0;
        }

        static decimal? CheckNumeric(JObj field, object raw, List<JObj> issues)
        {
            string name = F(field, "name"), tr = F(field, "label_tr"), en = F(field, "label_en");
            decimal? number = ToDecimal(raw);
            if (!number.HasValue)
            {
                issues.Add(Issue(name, tr + ": sayısal bir değer giriniz.", en + ": enter a numeric value."));
                return null;
            }
            if (F(field, "kind") == "integer" && DecimalPlaces(number.Value) > 0)
                issues.Add(Issue(name, tr + ": tam sayı olmalıdır.", en + ": must be a whole number."));

            object decimals = U.Get(field, "decimals");
            if (decimals != null && DecimalPlaces(number.Value) > Convert.ToInt32(decimals, U.Inv))
                issues.Add(Issue(name,
                    tr + ": virgülden sonra en fazla " + U.Str(decimals) + " hane olabilir. "
                       + "(EPDK: “Girilen Ondalık Değeri En Fazla Üç Hane Olabilir..”)",
                    en + ": at most " + U.Str(decimals) + " decimal places."));

            object minimum = U.Get(field, "minimum");
            if (minimum != null && number.Value < ToDecimal(minimum).Value)
                issues.Add(Issue(name, tr + ": " + U.Str(minimum) + " değerinden küçük olamaz.",
                                 en + ": cannot be lower than " + U.Str(minimum) + "."));

            object maximum = U.Get(field, "maximum");
            if (maximum != null && number.Value > ToDecimal(maximum).Value)
                issues.Add(Issue(name, tr + ": " + U.Str(maximum) + " değerinden büyük olamaz.",
                                 en + ": cannot be greater than " + U.Str(maximum) + "."));
            return number;
        }

        static List<JObj> CheckCommon(JObj table, JObj record, Dictionary<string, decimal> numbers)
        {
            var issues = new List<JObj>();
            foreach (var field in Fields(table))
            {
                string name = F(field, "name"), kind = F(field, "kind");
                string tr = F(field, "label_tr"), en = F(field, "label_en");
                object raw = U.Get(record, name);

                if (IsEmpty(raw))
                {
                    if (U.Truthy(U.Get(field, "required")))
                        issues.Add(Issue(name, tr + " zorunludur.", en + " is required."));
                    continue;
                }

                if (kind == "decimal" || kind == "integer")
                {
                    decimal? number = CheckNumeric(field, raw, issues);
                    if (number.HasValue) numbers[name] = number.Value;
                }
                else if (kind == "upper")
                {
                    if (HasLowercase(U.Str(raw).Trim()))
                        issues.Add(Issue(name,
                            tr + ": BÜYÜK HARFLERLE yazılmalı ve kısaltma yapılmamalıdır. "
                               + "(EPDK: “Unvanda kısaltma yapılmaması ve büyük harflerle yazılması gerekmektedir.”)",
                            en + ": must be written in UPPERCASE without abbreviations."));
                }
                else if (kind == "select")
                {
                    var allowed = (U.AsList(U.Get(field, "options")) ?? new List<object>())
                        .OfType<JObj>().Select(o => U.Str(U.Get(o, "value")))
                        .Distinct().OrderBy(v => v, StringComparer.Ordinal).ToList();
                    if (!allowed.Contains(U.Str(raw).Trim()))
                        issues.Add(Issue(name,
                            tr + ": yalnızca " + string.Join(" ya da ", allowed) + " değeri gönderilebilir.",
                            en + ": only " + string.Join(" or ", allowed) + " is accepted."));
                }
                else if (kind == "date")
                {
                    if (!DateRe.IsMatch(U.Str(raw).Trim()))
                        issues.Add(Issue(name, tr + ": tarih YYYY-AA-GG biçiminde olmalıdır.",
                                         en + ": date must be in YYYY-MM-DD format."));
                }
                else if (kind == "datetime")
                {
                    if (!DateTimeRe.IsMatch(U.Str(raw).Trim()))
                        issues.Add(Issue(name, tr + ": zaman YYYY-AA-GGTSS:DD:ss biçiminde olmalıdır.",
                                         en + ": must be in YYYY-MM-DDTHH:MM:SS format."));
                }
            }
            return issues;
        }

        static void CheckDailyDate(JObj record, DateTime now, List<JObj> issues)
        {
            string raw = U.StrOr(U.Get(record, "tarih")).Trim();
            if (!DateRe.IsMatch(raw)) return;
            DateTime value;
            if (!DateTime.TryParseExact(raw, "yyyy-MM-dd", U.Inv, DateTimeStyles.None, out value)) return;
            DateTime today = now.Date;
            if (value > today)
                issues.Add(Issue("tarih", "İleri tarihli veri gönderilemez.",
                                 "Future-dated records cannot be submitted."));
            else if (value < today.AddDays(-1))
                issues.Add(Issue("tarih",
                    "Yalnızca bugüne ve bir önceki güne ait veri gönderilebilir. "
                    + "(Mazeret girişi yapılmışsa bu uyarıyı yok sayabilirsiniz.)",
                    "Only today's and yesterday's data can normally be submitted.",
                    "warning"));
        }

        public static List<JObj> Validate(JObj table, JObj record, List<object> tanks,
                                          List<object> gtipList, DateTime now)
        {
            var numbers = new Dictionary<string, decimal>();
            var issues = CheckCommon(table, record, numbers);

            // GTİP kontrolü (liste yüklenebildiyse)
            string gtip = U.StrOr(U.Get(record, "petrolTuruGTIPNo")).Trim();
            if (gtip.Length > 0 && gtipList != null && gtipList.Count > 0)
            {
                var known = new HashSet<string>(gtipList.OfType<JObj>().Select(i => U.Str(U.Get(i, "gtipNo")).Trim()));
                if (known.Count > 0 && !known.Contains(gtip))
                    issues.Add(Issue("petrolTuruGTIPNo",
                        "Bu GTİP No sistemde tanımlı petrol türleri listesinde yok. (EPDK: “Girilen Petrol Türü Hatalıdır.”)",
                        "This GTIP number is not in the system's petroleum type list."));
            }

            string key = U.Str(U.Get(table, "key"));
            if (key == "dep1")
            {
                ValidateDep1(record, numbers, tanks, now, issues);
            }
            else
            {
                CheckDailyDate(record, now, issues);
                decimal stock;
                if (numbers.TryGetValue("gunBasiStokTon", out stock) && stock == 0m)
                    issues.Add(Issue("gunBasiStokTon", "Gün başı stok değeri 0 girilemez.", "Opening stock cannot be zero."));
                if (key == "dep2")
                {
                    string vkn = U.StrOr(U.Get(record, "vkn")).Trim();
                    if (vkn.Length > 0 && vkn.All(char.IsDigit) && vkn != "0" && vkn.Length != 10 && vkn.Length != 11)
                        issues.Add(Issue("vkn",
                            "Vergi kimlik numarası genellikle 10 hanelidir; girilen değeri kontrol ediniz.",
                            "Tax numbers are normally 10 digits; please check.", "warning"));
                }
            }
            return issues;
        }

        static void ValidateDep1(JObj record, Dictionary<string, decimal> numbers, List<object> tanks,
                                 DateTime now, List<JObj> issues)
        {
            string saat = NormalizeDateTime(U.Get(record, "saat"));
            DateTime moment;
            if (DateTimeRe.IsMatch(saat)
                && DateTime.TryParseExact(saat, "yyyy-MM-dd'T'HH:mm:ss", U.Inv, DateTimeStyles.None, out moment))
            {
                if ((moment.Minute != 0 && moment.Minute != 30) || moment.Second != 0)
                    issues.Add(Issue("saat",
                        "Yalnızca tam saat (:00) ve buçuklarda (:30) veri gönderilebilir. "
                        + "(EPDK: “Yarım ve tam saatlerde veri gönderilebilir.”)",
                        "Only :00 and :30 readings are accepted."));
                if (moment > now)
                    issues.Add(Issue("saat", "İleri tarihli veri gönderilemez.",
                                     "Future-dated records cannot be submitted."));
                else if (moment < now.AddHours(-24))
                    issues.Add(Issue("saat",
                        "Bir kayıt, ait olduğu saatten sonraki 24 saat içinde gönderilmelidir. "
                        + "(EPDK: “Veri ekleme süreniz dolmuştur.”)",
                        "A record must be sent within 24 hours of its own timestamp.", "warning"));
            }

            decimal m3Value, tonValue, densityValue;
            bool hasM3 = numbers.TryGetValue("tankStokM3", out m3Value);
            bool hasTon = numbers.TryGetValue("tankStokTon", out tonValue);
            bool hasDensity = numbers.TryGetValue("petrolTuruYogunluk", out densityValue);

            if (hasM3 && hasTon && tonValue > m3Value * 2)
                issues.Add(Issue("tankStokTon",
                    "Ton değeri, m³ değerinin 2 katından fazla olamaz (yoğunluk en fazla 2 ton/m³ olabilir).",
                    "Tonnes cannot exceed twice the m³ value (max density 2 t/m³)."));

            if (hasDensity)
            {
                bool emptyTank = (!hasM3 || m3Value == 0m) && (!hasTon || tonValue == 0m);
                if (densityValue == 0m && !emptyTank)
                    issues.Add(Issue("petrolTuruYogunluk",
                        "Yoğunluk yalnızca m³ ve ton değerleri 0 olan boş tanklar için 0 olabilir. "
                        + "(EPDK: “Girilen Yoğunluk Değeri Hatalıdır.”)",
                        "Density may be 0 only when both m³ and tonnes are 0."));
                else if (densityValue != 0m && (densityValue < 100m || densityValue > 2000m))
                    issues.Add(Issue("petrolTuruYogunluk", "Yoğunluk 100 ile 2000 arasında olmalıdır.",
                                     "Density must be between 100 and 2000."));
            }

            string tankNo = U.StrOr(U.Get(record, "tankNumarasi")).Trim();
            if (tankNo.Length > 0 && tanks != null && tanks.Count > 0)
            {
                var catalog = new Dictionary<string, JObj>();
                foreach (var tank in tanks.OfType<JObj>()) catalog[U.Str(U.Get(tank, "tankNo")).Trim()] = tank;

                if (catalog.Count > 0 && !catalog.ContainsKey(tankNo))
                {
                    issues.Add(Issue("tankNumarasi",
                        "Bu tank numarası lisansa kayıtlı tanklar listesinde yok. (EPDK: “Girilen Tank Numarası Hatalı.”)",
                        "This tank number is not in the licensed tank list."));
                }
                else if (catalog.ContainsKey(tankNo) && hasM3)
                {
                    decimal? capacity = ToDecimal(U.Get(catalog[tankNo], "kapasiteM3"));
                    if (capacity.HasValue && capacity.Value > 0m && m3Value > capacity.Value)
                        issues.Add(Issue("tankStokM3",
                            "Tank kapasitesi " + U.Str(capacity.Value) + " m³; bundan fazla ürün gönderilemez.",
                            "Tank capacity is " + U.Str(capacity.Value) + " m³; stock cannot exceed it."));
                }
            }
        }

        /// <summary>Servise gönderilecek gövdeyi normalize eder (sayı, tarih, boşluk).</summary>
        public static JObj Clean(JObj table, JObj record)
        {
            var result = new JObj();
            foreach (var field in Fields(table))
            {
                string name = F(field, "name"), kind = F(field, "kind");
                if (!record.ContainsKey(name)) continue;
                object raw = record[name];
                if (IsEmpty(raw)) continue;

                if (kind == "decimal")
                {
                    decimal? number = ToDecimal(raw);
                    result[name] = number.HasValue ? (object)Normalize(number.Value) : raw;
                }
                else if (kind == "integer" || kind == "select")
                {
                    decimal? number = ToDecimal(raw);
                    if (number.HasValue && DecimalPlaces(number.Value) == 0
                        && Math.Abs(number.Value) <= long.MaxValue)
                        result[name] = (long)decimal.Truncate(number.Value);
                    else
                        result[name] = raw;
                }
                else if (kind == "datetime")
                {
                    result[name] = NormalizeDateTime(raw);
                }
                else
                {
                    result[name] = U.Str(raw).Trim();
                }
            }
            return result;
        }
    }

    // ============================================================= uçlar
    /// <summary>/api altındaki uçlar — epdk/server.py ile aynı sözleşme.</summary>
    sealed class Api
    {
        readonly Schema _schema;
        readonly Session _session = new Session();
        readonly ActivityLog _log;
        readonly object _settingsGate = new object();
        JObj _settings;

        public Api(Schema schema)
        {
            _schema = schema;
            _settings = Settings.Load();
            _log = new ActivityLog(Path.Combine(Settings.Home(), "activity.jsonl"),
                                   Convert.ToInt32(U.Get(_settings, "log_limit") ?? 2000, U.Inv));
        }

        Session RequireSession()
        {
            if (!_session.Active) throw new ApiException("Oturum açık değil. Lütfen giriş yapın.", 401);
            return _session;
        }

        JObj Table(string key)
        {
            var table = _schema.Table(key);
            if (table == null) throw new ApiException("Bilinmeyen tablo: " + key, 404);
            return table;
        }

        static string MessageOr(JObj result, string fallback)
        {
            object message = U.Get(result, "message");
            return U.Truthy(message) ? U.Str(message) : fallback;
        }

        static string Single(Query query, string key, string fallback)
        {
            List<string> values;
            return query.TryGetValue(key, out values) && values.Count > 0 ? values[0] : fallback;
        }

        static bool Flag(Query query, string key)
        {
            string value = Single(query, key, "").ToLowerInvariant();
            return value == "1" || value == "true" || value == "yes" || value == "evet";
        }

        /// <summary>Servis çağrısını çalıştırır, sonucu işlem geçmişine yazar.</summary>
        JObj Call(string action, string tableKey, string endpoint, Func<string, ApiResult> runner,
                  object requestPayload, string recordId)
        {
            var session = RequireSession();
            ApiResult result;
            try
            {
                string token = session.EnsureToken();
                result = runner(token);
            }
            catch (EpdkException error)
            {
                _log.Record(action, false, session.EnvironmentKey, session.Username, tableKey, endpoint,
                            error.Status, error.Message, recordId, requestPayload,
                            U.Truthy(error.Payload) ? error.Payload : null, null);
                throw new ApiException(error.Message, error.Status.HasValue ? 400 : 502);
            }

            _log.Record(action, result.Success, session.EnvironmentKey, session.Username, tableKey, endpoint,
                        result.Status, U.StrOr(result.Message), recordId, requestPayload, result.Raw,
                        result.DurationMs);
            return result.AsDict();
        }

        // ---------------------------------------------------------- oturum
        JObj CopySettings()
        {
            lock (_settingsGate) { return new JObj(_settings); }
        }

        public JObj Meta(JObj payload, Query query)
        {
            return U.Obj(
                "app", U.Obj("name", Program.AppName, "version", Program.Version,
                             "runtime", ".NET " + Environment.Version),
                "environments", _schema.Environments,
                "settings", CopySettings(),
                "schema", _schema.Describe(),
                "session", _session.Describe());
        }

        public JObj SessionInfo(JObj payload, Query query)
        {
            return U.Obj("session", _session.Describe());
        }

        public JObj Login(JObj payload, Query query)
        {
            string username = U.StrOr(U.Get(payload, "username")).Trim();
            string password = U.StrOr(U.Get(payload, "password"));
            string environment;
            string customUrl = U.StrOr(U.Get(payload, "customBaseUrl")).Trim();
            string baseUrl;
            int timeout;
            bool autoRenew;

            lock (_settingsGate)
            {
                environment = U.StrOr(U.Get(payload, "environment"));
                if (environment.Length == 0) environment = U.Str(U.Get(_settings, "environment"));
            }
            if (username.Length == 0 || password.Length == 0)
                throw new ApiException("Kullanıcı adı ve parola zorunludur.");

            lock (_settingsGate)
            {
                _settings["environment"] = environment;
                if (environment == "custom")
                {
                    if (customUrl.Length == 0) throw new ApiException("Özel ortam için adres girmelisiniz.");
                    _settings["custom_base_url"] = customUrl;
                }
                baseUrl = Settings.ResolveBaseUrl(_settings, _schema.Environments);
                timeout = Convert.ToInt32(U.Get(_settings, "timeout") ?? 45, U.Inv);
                autoRenew = payload.ContainsKey("autoRenew")
                    ? U.Truthy(payload["autoRenew"])
                    : U.Truthy(U.Get(_settings, "auto_renew") ?? true);
            }
            if (baseUrl.Length == 0) throw new ApiException("Servis adresi belirlenemedi.");

            var masked = U.Obj("username", username, "password", "••••••");
            JObj info;
            try
            {
                info = _session.Login(baseUrl, environment, username, password, timeout, autoRenew);
            }
            catch (EpdkException error)
            {
                _log.Record("login", false, environment, username, "", "authentication/login",
                            error.Status, error.Message, null, masked, null, null);
                throw new ApiException(error.Message, 401);
            }

            _log.Record("login", true, environment, username, "", "authentication/login",
                        null, "Oturum açıldı", null, masked, null, null);

            // Kullanıcı adı, ortam ve müşteri profili saklanır (parola değil).
            lock (_settingsGate)
            {
                _settings["username"] = username;
                _settings["auto_renew"] = autoRenew;
                Settings.UpsertProfile(_settings, username, environment, customUrl);
                _settings = Settings.Save(_settings);
            }
            return U.Obj("session", info);
        }

        public JObj Logout(JObj payload, Query query)
        {
            _session.Logout();
            return U.Obj("session", _session.Describe());
        }

        // --------------------------------------------------------- ayarlar
        public JObj DeleteProfile(JObj payload, Query query)
        {
            lock (_settingsGate)
            {
                Settings.RemoveProfile(_settings, U.StrOr(U.Get(payload, "username")),
                                       U.StrOr(U.Get(payload, "environment")));
                _settings = Settings.Save(_settings);
                return U.Obj("settings", new JObj(_settings));
            }
        }

        public JObj UpdateSettings(JObj payload, Query query)
        {
            lock (_settingsGate)
            {
                _settings = Settings.Save(payload ?? new JObj());
                _log.Limit = Math.Max(100, Convert.ToInt32(U.Get(_settings, "log_limit") ?? 2000, U.Inv));
                return U.Obj("settings", new JObj(_settings));
            }
        }

        // ------------------------------------------------------ referanslar
        public JObj Tanks(JObj payload, Query query)
        {
            return Lookup("tanks", "lisansakayitlitanklistesisorgu", "tank-listesi", Flag(query, "refresh"));
        }

        public JObj Gtip(JObj payload, Query query)
        {
            return Lookup("gtip", "petrolturlerisorgu", "petrol-turleri", Flag(query, "refresh"));
        }

        JObj Lookup(string cacheKey, string endpoint, string action, bool refresh)
        {
            var session = RequireSession();
            if (!refresh)
            {
                object cached = session.Cached(cacheKey);
                if (cached != null) return U.Obj("data", cached, "cached", true);
            }
            var result = Call(action, "", endpoint,
                token => session.Client().Query(endpoint, session.Username, token),
                U.Obj("kullanici", session.Username), null);
            if (!U.Truthy(result["success"])) throw new ApiException(MessageOr(result, "Liste alınamadı."), 400);
            object data = U.Truthy(result["data"]) ? result["data"] : new List<object>();
            session.Cache(cacheKey, data);
            return U.Obj("data", data, "cached", false);
        }

        // --------------------------------------------------------- tablolar
        public JObj ListRecords(string tableKey, JObj payload, Query query)
        {
            var table = Table(tableKey);
            var session = RequireSession();
            string endpoint = U.Str(U.Get(table, "path")) + "/" + U.Str(U.Get(table, "query_path"));
            var result = Call("sorgu", tableKey, endpoint,
                token => session.Client().Query(endpoint, session.Username, token),
                U.Obj("kullanici", session.Username), null);
            if (!U.Truthy(result["success"])) throw new ApiException(MessageOr(result, "Kayıtlar alınamadı."), 400);
            return U.Obj("data", U.Truthy(result["data"]) ? result["data"] : new List<object>(),
                         "fetchedAt", DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff", U.Inv));
        }

        public JObj ListServices(string tableKey, JObj payload, Query query)
        {
            var table = Table(tableKey);
            var extra = U.AsObj(U.Get(table, "extra_query"));
            if (extra == null) throw new ApiException("Bu tablo için hizmet sorgusu tanımlı değil.", 404);
            var session = RequireSession();
            string endpoint = U.Str(U.Get(table, "path")) + "/" + U.Str(U.Get(extra, "path"));
            var result = Call("hizmet-sorgu", tableKey, endpoint,
                token => session.Client().Query(endpoint, session.Username, token),
                U.Obj("kullanici", session.Username), null);
            if (!U.Truthy(result["success"])) throw new ApiException(MessageOr(result, "Kayıtlar alınamadı."), 400);
            return U.Obj("data", U.Truthy(result["data"]) ? result["data"] : new List<object>());
        }

        List<JObj> RunValidation(JObj table, JObj record)
        {
            bool dep1 = U.Str(U.Get(table, "key")) == "dep1";
            return Validator.Validate(table, record,
                dep1 ? U.AsList(_session.Cached("tanks")) : null,
                U.AsList(_session.Cached("gtip")),
                DateTime.Now);
        }

        static void Split(List<JObj> issues, out List<JObj> errors, out List<JObj> warnings)
        {
            errors = issues.Where(i => U.Str(i["level"]) == "error").ToList();
            warnings = issues.Where(i => U.Str(i["level"]) == "warning").ToList();
        }

        public JObj ValidateRecord(string tableKey, JObj payload, Query query)
        {
            var table = Table(tableKey);
            List<JObj> errors, warnings;
            Split(RunValidation(table, U.AsObj(U.Get(payload, "record")) ?? new JObj()), out errors, out warnings);
            return U.Obj("errors", errors, "warnings", warnings, "valid", errors.Count == 0);
        }

        public JObj SaveRecord(string tableKey, JObj payload, Query query)
        {
            return Write(tableKey, payload, "save");
        }

        public JObj UpdateRecord(string tableKey, JObj payload, Query query)
        {
            return Write(tableKey, payload, "update");
        }

        public JObj DeleteRecord(string tableKey, JObj payload, Query query)
        {
            var table = Table(tableKey);
            var session = RequireSession();
            string recordId = U.StrOr(U.Get(payload, "id")).Trim();
            if (recordId.Length == 0) throw new ApiException("Silinecek kaydın ID değeri gereklidir.");
            string path = U.Str(U.Get(table, "path"));

            var result = Call("silme", tableKey, path + "/delete",
                token => session.Client().Delete(path, recordId, session.Username, token),
                U.Obj("id", recordId, "kullanici", session.Username), recordId);
            if (!U.Truthy(result["success"])) throw new ApiException(MessageOr(result, "Kayıt silinemedi."), 400);
            return U.Obj("result", result);
        }

        /// <summary>Birden fazla kaydı sırayla gönderir; her satırın sonucunu döndürür.</summary>
        public JObj BulkSave(string tableKey, JObj payload, Query query)
        {
            Table(tableKey);
            var rows = U.AsList(U.Get(payload, "records"));
            if (rows == null || rows.Count == 0) throw new ApiException("Gönderilecek kayıt bulunamadı.");
            if (rows.Count > 500) throw new ApiException("Tek seferde en fazla 500 kayıt gönderilebilir.");
            bool ignoreWarnings = !payload.ContainsKey("ignoreWarnings") || U.Truthy(payload["ignoreWarnings"]);

            var results = new List<object>();
            int succeeded = 0;
            for (int index = 0; index < rows.Count; index++)
            {
                var entry = U.Obj("index", index);
                try
                {
                    var outcome = Write(tableKey,
                        U.Obj("record", U.AsObj(rows[index]) ?? new JObj(), "ignoreWarnings", ignoreWarnings),
                        "save");
                    object id = U.Get(U.AsObj(outcome["result"]), "message");
                    entry["success"] = true;
                    entry["id"] = id;
                    entry["message"] = id;
                    succeeded++;
                }
                catch (ApiException error)
                {
                    entry["success"] = false;
                    entry["message"] = error.Message;
                    entry["details"] = error.Details;
                }
                results.Add(entry);
            }
            return U.Obj("results", results,
                         "summary", U.Obj("total", results.Count, "succeeded", succeeded,
                                          "failed", results.Count - succeeded));
        }

        JObj Write(string tableKey, JObj payload, string mode)
        {
            var table = Table(tableKey);
            var session = RequireSession();
            var record = U.AsObj(U.Get(payload, "record")) ?? new JObj();
            bool ignoreWarnings = !payload.ContainsKey("ignoreWarnings") || U.Truthy(payload["ignoreWarnings"]);

            List<JObj> errors, warnings;
            Split(RunValidation(table, record), out errors, out warnings);
            if (errors.Count > 0)
                throw new ApiException("Kayıt EPDK kurallarına uymuyor; gönderilmedi.", 422,
                                       U.Obj("errors", errors, "warnings", warnings));
            if (warnings.Count > 0 && !ignoreWarnings)
                throw new ApiException("Kayıtta dikkat edilmesi gereken noktalar var.", 422,
                                       U.Obj("errors", new List<JObj>(), "warnings", warnings));

            var body = Validator.Clean(table, record);
            body["kullanici"] = session.Username;

            string recordId = U.StrOr(U.Get(record, "id")).Trim();
            if (mode == "update")
            {
                if (recordId.Length == 0) throw new ApiException("Güncelleme için kaydın ID değeri gereklidir.");
                body["id"] = recordId;
            }

            string path = U.Str(U.Get(table, "path"));
            Func<string, ApiResult> runner;
            if (mode == "save") runner = token => session.Client().Save(path, body, token);
            else runner = token => session.Client().Update(path, body, token);

            var result = Call(mode == "save" ? "kayit" : "guncelleme", tableKey, path + "/" + mode,
                              runner, body, recordId.Length > 0 ? recordId : null);
            if (!U.Truthy(result["success"])) throw new ApiException(MessageOr(result, "İşlem başarısız."), 400);
            return U.Obj("result", result, "warnings", warnings);
        }

        // -------------------------------------------------------- geçmiş
        public JObj Log(JObj payload, Query query)
        {
            int limit;
            if (!int.TryParse(Single(query, "limit", "200"), NumberStyles.Integer, U.Inv, out limit)) limit = 200;
            var entries = _log.List(limit, Single(query, "table", ""), Flag(query, "failures"), Single(query, "q", ""));
            return U.Obj("entries", entries, "stats", _log.Stats());
        }

        public JObj ClearLog(JObj payload, Query query)
        {
            _log.Clear();
            return U.Obj("cleared", true);
        }

        // ---------------------------------------------------------- özet
        public JObj Dashboard(JObj payload, Query query)
        {
            string today = DateTime.Now.ToString("yyyy-MM-dd", U.Inv);
            string yesterday = DateTime.Now.AddDays(-1).ToString("yyyy-MM-dd", U.Inv);
            var summary = new JObj();

            if (_session.Active)
            {
                foreach (var table in _schema.Tables)
                {
                    string key = U.Str(U.Get(table, "key"));
                    string endpoint = U.Str(U.Get(table, "path")) + "/" + U.Str(U.Get(table, "query_path"));
                    try
                    {
                        var result = Call("sorgu", key, endpoint,
                            token => _session.Client().Query(endpoint, _session.Username, token),
                            U.Obj("kullanici", _session.Username), null);
                        bool ok = U.Truthy(result["success"]);
                        var rows = ok ? (U.AsList(result["data"]) ?? new List<object>()) : new List<object>();
                        var objects = rows.OfType<JObj>().ToList();
                        summary[key] = U.Obj(
                            "ok", ok,
                            "message", result["message"],
                            "total", rows.Count,
                            "today", CountForDay(objects, today),
                            "yesterday", CountForDay(objects, yesterday),
                            "lastRecord", Latest(objects),
                            "rows", rows);
                    }
                    catch (ApiException error)
                    {
                        summary[key] = U.Obj("ok", false, "message", error.Message, "total", 0, "today", 0,
                                             "yesterday", 0, "lastRecord", null, "rows", new List<object>());
                    }
                }
            }

            return U.Obj(
                "summary", summary,
                "tanks", TanksForCharts(),
                "activity", _log.List(8, "", false, ""),
                "stats", _log.Stats(),
                "session", _session.Describe());
        }

        object TanksForCharts()
        {
            if (!_session.Active) return new List<object>();
            object cached = _session.Cached("tanks");
            if (cached != null) return cached;
            try
            {
                return Lookup("tanks", "lisansakayitlitanklistesisorgu", "tank-listesi", false)["data"];
            }
            catch (ApiException)
            {
                return new List<object>();
            }
        }

        static int CountForDay(List<JObj> rows, string day)
        {
            return rows.Count(row =>
            {
                object value = U.Truthy(U.Get(row, "tarih")) ? U.Get(row, "tarih") : U.Get(row, "saat");
                return U.StrOr(value).StartsWith(day);
            });
        }

        static object Latest(List<JObj> rows)
        {
            var stamps = rows.Select(r => U.StrOr(U.Get(r, "islemZamani"))).Where(s => s.Length > 0).ToList();
            if (stamps.Count == 0) return null;
            return stamps.OrderBy(s => s, StringComparer.Ordinal).Last();
        }

        // ------------------------------------------------------- yönlendirme
        public JObj Route(string method, string path, JObj payload, Query query)
        {
            var parts = path.Trim('/').Split('/').Skip(1).Select(Uri.UnescapeDataString).ToList();
            if (parts.Count == 0) throw new ApiException("Bilinmeyen uç.", 404);
            string head = parts[0];
            var rest = parts.Skip(1).ToList();

            if (head == "meta" && method == "GET") return Meta(payload, query);
            if (head == "session" && method == "GET") return SessionInfo(payload, query);
            if (head == "login" && method == "POST") return Login(payload, query);
            if (head == "logout" && method == "POST") return Logout(payload, query);
            if (head == "settings" && method == "POST") return UpdateSettings(payload, query);
            if (head == "profiles" && method == "POST" && rest.Count == 1 && rest[0] == "remove")
                return DeleteProfile(payload, query);
            if (head == "dashboard" && method == "GET") return Dashboard(payload, query);
            if (head == "log")
            {
                if (method == "GET" && rest.Count == 0) return Log(payload, query);
                if (method == "POST" && rest.Count == 1 && rest[0] == "clear") return ClearLog(payload, query);
            }
            if (head == "lookup" && method == "GET" && rest.Count > 0)
            {
                if (rest[0] == "tanks") return Tanks(payload, query);
                if (rest[0] == "gtip") return Gtip(payload, query);
            }
            if (head == "table" && rest.Count > 0)
            {
                string tableKey = rest[0];
                string action = rest.Count > 1 ? rest[1] : "";
                if (method == "GET" && action == "") return ListRecords(tableKey, payload, query);
                if (method == "GET" && action == "services") return ListServices(tableKey, payload, query);
                if (method == "POST" && action == "") return SaveRecord(tableKey, payload, query);
                if (method == "PUT" && action == "") return UpdateRecord(tableKey, payload, query);
                if (method == "POST" && action == "delete") return DeleteRecord(tableKey, payload, query);
                if (method == "POST" && action == "bulk") return BulkSave(tableKey, payload, query);
                if (method == "POST" && action == "validate") return ValidateRecord(tableKey, payload, query);
            }
            throw new ApiException("Bilinmeyen uç: " + method + " " + path, 404);
        }
    }

    // ===================================================== HTTP sunucusu
    /// <summary>
    /// Yalnızca bu bilgisayarı (127.0.0.1) dinleyen küçük HTTP sunucusu.
    /// HttpListener yerine TcpListener kullanılır: HttpListener yönetici
    /// yetkisi ya da URL ACL kaydı isteyebilir, TcpListener istemez.
    /// </summary>
    sealed class HttpServer
    {
        const int MaxBody = 8 * 1024 * 1024;
        const string ClientHeader = "X-Epdk-Client";
        readonly TcpListener _listener;
        readonly Api _api;
        readonly string _webRoot;

        sealed class Request
        {
            public string Method;
            public string Path;
            public Query Query;
            public Dictionary<string, string> Headers;
            public byte[] Body;
            public bool TooLarge;
        }

        sealed class Response
        {
            public int Status;
            public string ContentType;
            public byte[] Body;
            public bool Static;
        }

        public HttpServer(IPAddress address, int port, Api api, string webRoot)
        {
            _listener = new TcpListener(address, port);
            _api = api;
            _webRoot = Path.GetFullPath(webRoot);
        }

        public void Start()
        {
            _listener.Start();
        }

        public void Serve()
        {
            while (true)
            {
                TcpClient client;
                try
                {
                    client = _listener.AcceptTcpClient();
                }
                catch (SocketException)
                {
                    continue;
                }
                ThreadPool.QueueUserWorkItem(Handle, client);
            }
        }

        void Handle(object state)
        {
            var client = (TcpClient)state;
            try
            {
                client.ReceiveTimeout = 30000;
                client.SendTimeout = 30000;
                NetworkStream stream = client.GetStream();
                var request = ReadRequest(stream);
                if (request == null) return;
                Respond(stream, Dispatch(request));
            }
            catch (Exception)
            {
                // Tarayıcı bağlantıyı erken kapattıysa yapılacak bir şey yok.
            }
            finally
            {
                try { client.Close(); } catch (Exception) { }
            }
        }

        static int HeaderEnd(byte[] data, int length)
        {
            for (int i = 3; i < length; i++)
            {
                if (data[i - 3] == '\r' && data[i - 2] == '\n' && data[i - 1] == '\r' && data[i] == '\n') return i - 3;
            }
            return -1;
        }

        static Request ReadRequest(NetworkStream stream)
        {
            var buffer = new MemoryStream();
            var chunk = new byte[8192];
            int headerEnd = -1;
            while (headerEnd < 0)
            {
                int read = stream.Read(chunk, 0, chunk.Length);
                if (read <= 0) return null;
                buffer.Write(chunk, 0, read);
                headerEnd = HeaderEnd(buffer.GetBuffer(), (int)buffer.Length);
                if (headerEnd < 0 && buffer.Length > 65536) return null;
            }

            byte[] all = buffer.ToArray();
            string[] lines = Encoding.UTF8.GetString(all, 0, headerEnd).Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] first = lines[0].Split(' ');
            if (first.Length < 2) return null;

            var request = new Request
            {
                Method = first[0].ToUpperInvariant(),
                Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase),
                Query = new Query(),
            };

            string target = first[1];
            int questionMark = target.IndexOf('?');
            request.Path = questionMark >= 0 ? target.Substring(0, questionMark) : target;
            if (questionMark >= 0) ParseQuery(target.Substring(questionMark + 1), request.Query);

            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon > 0) request.Headers[lines[i].Substring(0, colon).Trim()] = lines[i].Substring(colon + 1).Trim();
            }

            long length = 0;
            string lengthText;
            if (request.Headers.TryGetValue("Content-Length", out lengthText))
                long.TryParse(lengthText, NumberStyles.Integer, U.Inv, out length);
            if (length > MaxBody)
            {
                request.TooLarge = true;
                request.Body = new byte[0];
                return request;
            }

            var body = new MemoryStream();
            int bodyStart = headerEnd + 4;
            body.Write(all, bodyStart, all.Length - bodyStart);
            while (body.Length < length)
            {
                int read = stream.Read(chunk, 0, chunk.Length);
                if (read <= 0) break;
                body.Write(chunk, 0, read);
            }
            request.Body = body.ToArray();
            return request;
        }

        static void ParseQuery(string text, Query query)
        {
            foreach (var pair in text.Split('&'))
            {
                if (pair.Length == 0) continue;
                int equals = pair.IndexOf('=');
                string key = Decode(equals >= 0 ? pair.Substring(0, equals) : pair);
                string value = equals >= 0 ? Decode(pair.Substring(equals + 1)) : "";
                if (value.Length == 0) continue;   // parse_qs boş değerleri atlar
                List<string> values;
                if (!query.TryGetValue(key, out values)) query[key] = values = new List<string>();
                values.Add(value);
            }
        }

        static string Decode(string text)
        {
            return Uri.UnescapeDataString(text.Replace('+', ' '));
        }

        Response Dispatch(Request request)
        {
            if (request.Path.StartsWith("/api/"))
            {
                if (request.Method != "GET" && request.Method != "POST" && request.Method != "PUT")
                    return JsonResponse(U.Obj("ok", false, "error", "Desteklenmeyen yöntem."), 501);

                // Basit CSRF koruması: başka bir site tarayıcıdan bu başlığı gönderemez.
                string client;
                request.Headers.TryGetValue(ClientHeader, out client);
                if (client != "web")
                    return JsonResponse(U.Obj("ok", false, "error", "Geçersiz istek kaynağı."), 403);

                try
                {
                    JObj payload = request.Method == "GET" ? new JObj() : ReadJson(request);
                    var result = _api.Route(request.Method, request.Path, payload, request.Query);
                    var output = U.Obj("ok", true);
                    foreach (var pair in result) output[pair.Key] = pair.Value;
                    return JsonResponse(output, 200);
                }
                catch (ApiException error)
                {
                    return JsonResponse(U.Obj("ok", false, "error", error.Message, "details", error.Details), error.Status);
                }
                catch (Exception error)
                {
                    Console.Error.WriteLine(error);
                    return JsonResponse(U.Obj("ok", false, "error", "Beklenmeyen hata: " + error.Message), 500);
                }
            }

            if (request.Method == "GET") return ServeStatic(request.Path);
            return JsonResponse(U.Obj("error", "Bulunamadı"), 404);
        }

        static JObj ReadJson(Request request)
        {
            if (request.TooLarge) throw new ApiException("İstek gövdesi çok büyük.", 413);
            if (request.Body.Length == 0) return new JObj();
            object data;
            try
            {
                data = Json.Parse(Encoding.UTF8.GetString(request.Body));
            }
            catch (Exception)
            {
                throw new ApiException("Geçersiz JSON gövdesi.");
            }
            return data as JObj ?? U.Obj("value", data);
        }

        Response ServeStatic(string rawPath)
        {
            string relative;
            try { relative = Uri.UnescapeDataString(rawPath); }
            catch (Exception) { return JsonResponse(U.Obj("error", "Erişim reddedildi."), 403); }
            relative = relative.Replace('\\', '/').TrimStart('/');
            if (relative.Length == 0 || relative == ".") relative = "index.html";

            string full;
            try
            {
                full = Path.GetFullPath(Path.Combine(_webRoot, relative.Replace('/', Path.DirectorySeparatorChar)));
            }
            catch (Exception)
            {
                return JsonResponse(U.Obj("error", "Erişim reddedildi."), 403);
            }

            string root = _webRoot.EndsWith(Path.DirectorySeparatorChar.ToString())
                ? _webRoot
                : _webRoot + Path.DirectorySeparatorChar;
            if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                return JsonResponse(U.Obj("error", "Erişim reddedildi."), 403);

            if (!File.Exists(full))
            {
                // Tek sayfa uygulaması: bilinmeyen yol arayüze yönlendirilir.
                full = Path.Combine(_webRoot, "index.html");
                if (!File.Exists(full)) return JsonResponse(U.Obj("error", "Arayüz dosyaları bulunamadı."), 404);
            }

            return new Response { Status = 200, ContentType = Mime(full), Body = File.ReadAllBytes(full), Static = true };
        }

        static string Mime(string path)
        {
            switch (Path.GetExtension(path).ToLowerInvariant())
            {
                case ".html": return "text/html; charset=utf-8";
                case ".js": return "text/javascript; charset=utf-8";
                case ".css": return "text/css; charset=utf-8";
                case ".json": return "application/json; charset=utf-8";
                case ".svg": return "image/svg+xml";
                case ".png": return "image/png";
                case ".ico": return "image/x-icon";
                case ".csv": return "text/csv; charset=utf-8";
                default: return "application/octet-stream";
            }
        }

        static Response JsonResponse(JObj payload, int status)
        {
            return new Response
            {
                Status = status,
                ContentType = "application/json; charset=utf-8",
                Body = U.Utf8.GetBytes(Json.Write(payload)),
            };
        }

        static string ReasonPhrase(int status)
        {
            switch (status)
            {
                case 200: return "OK";
                case 400: return "Bad Request";
                case 401: return "Unauthorized";
                case 403: return "Forbidden";
                case 404: return "Not Found";
                case 413: return "Payload Too Large";
                case 422: return "Unprocessable Entity";
                case 501: return "Not Implemented";
                case 502: return "Bad Gateway";
                default: return status >= 500 ? "Internal Server Error" : "Status";
            }
        }

        static void Respond(Stream stream, Response response)
        {
            var head = new StringBuilder();
            head.Append("HTTP/1.1 ").Append(response.Status.ToString(U.Inv)).Append(' ')
                .Append(ReasonPhrase(response.Status)).Append("\r\n");
            head.Append("Content-Type: ").Append(response.ContentType).Append("\r\n");
            head.Append("Content-Length: ").Append(response.Body.Length.ToString(U.Inv)).Append("\r\n");
            head.Append("Cache-Control: ").Append(response.Static ? "no-cache" : "no-store").Append("\r\n");
            head.Append("X-Content-Type-Options: nosniff\r\n");
            head.Append("Connection: close\r\n\r\n");
            byte[] headBytes = Encoding.ASCII.GetBytes(head.ToString());
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(response.Body, 0, response.Body.Length);
            stream.Flush();
        }
    }

    // ========================================================== giriş
    static class Program
    {
        public const string AppName = "EPDK Petrol Stok İzleme";
        public const string Version = "1.0.0";

        static int Main(string[] args)
        {
            try { Console.OutputEncoding = U.Utf8; }
            catch (Exception) { }

            int port = 8787;
            string host = "127.0.0.1";
            bool openBrowser = true;
            string webRoot = null;

            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                if (arg == "--port" && i + 1 < args.Length) int.TryParse(args[++i], out port);
                else if (arg == "--host" && i + 1 < args.Length) host = args[++i];
                else if (arg == "--web" && i + 1 < args.Length) webRoot = args[++i];
                else if (arg == "--no-browser") openBrowser = false;
                else if (arg == "--version")
                {
                    Console.WriteLine(AppName + " " + Version + " (.NET)");
                    return 0;
                }
            }

            webRoot = webRoot ?? FindWebRoot();
            if (webRoot == null)
            {
                Console.Error.WriteLine("HATA: Arayuz dosyalari (web klasoru) bulunamadi.");
                Console.Error.WriteLine("EpdkStok.exe dosyasi epdk\\windows klasorunde olmali.");
                return 1;
            }

            Schema schema;
            try
            {
                schema = Schema.Load(webRoot);
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("HATA: schema.json okunamadi -> " + error.Message);
                return 1;
            }

            IPAddress address;
            if (!IPAddress.TryParse(host == "localhost" ? "127.0.0.1" : host, out address))
            {
                Console.Error.WriteLine("HATA: Gecersiz adres: " + host);
                return 1;
            }

            var server = new HttpServer(address, port, new Api(schema), webRoot);
            string url = "http://" + host + ":" + port.ToString(U.Inv) + "/";
            try
            {
                server.Start();
            }
            catch (SocketException error)
            {
                // İkinci kez başlatıldıysa yeni sunucu açılmaz; açık olana gidilir.
                if (IsOurAppRunning(url))
                {
                    Console.WriteLine("  Uygulama zaten acik; tarayici aciliyor: " + url);
                    if (openBrowser) OpenBrowser(url);
                    return 0;
                }
                Console.Error.WriteLine("HATA: " + host + ":" + port + " dinlenemedi -> " + error.Message);
                Console.Error.WriteLine("Port baska bir program tarafindan kullaniliyor. Baska bir port deneyin:");
                Console.Error.WriteLine("  BASLAT.bat --port 8788");
                return 1;
            }

            Console.WriteLine();
            Console.WriteLine("  EPDK Petrol Stok Izleme v" + Version + "  (Windows, kurulumsuz surum)");
            Console.WriteLine("  ->  Arayuz : " + url);
            Console.WriteLine("  ->  Ayarlar: " + Settings.Home());
            Console.WriteLine("  ->  Kapatmak icin bu pencereyi kapatin (Ctrl+C)");
            Console.WriteLine();

            if (openBrowser) OpenBrowser(url);
            server.Serve();
            return 0;
        }

        /// <summary>Port meşgulse, dinleyen programın bu uygulama olup olmadığını sorar.</summary>
        static bool IsOurAppRunning(string url)
        {
            try
            {
                var headers = new Dictionary<string, string>();
                headers["X-Epdk-Client"] = "web";
                var reply = RawHttp.Send("GET", new Uri(url + "api/meta"), new byte[0], headers, 3000);
                var meta = Json.Parse(reply.Body) as JObj;
                return U.Str(U.Get(U.AsObj(U.Get(meta, "app")), "name")) == AppName;
            }
            catch (Exception)
            {
                return false;
            }
        }

        static string FindWebRoot()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            var candidates = new[]
            {
                Path.Combine(baseDir, "..", "web"),
                Path.Combine(baseDir, "web"),
                Path.Combine(Environment.CurrentDirectory, "web"),
                Path.Combine(Environment.CurrentDirectory, "epdk", "web"),
            };
            foreach (var candidate in candidates)
            {
                string full = Path.GetFullPath(candidate);
                if (File.Exists(Path.Combine(full, "index.html")) && File.Exists(Path.Combine(full, "schema.json")))
                    return full;
            }
            return null;
        }

        /// <summary>
        /// Arayüz modern bir tarayıcı ister. Eski Windows'ta varsayılan tarayıcı
        /// Internet Explorer olabileceğinden önce Chrome, Edge ve Firefox aranır.
        /// </summary>
        static string FindModernBrowser()
        {
            if (Path.DirectorySeparatorChar != '\\') return null;
            var roots = new[]
            {
                Environment.GetEnvironmentVariable("ProgramFiles"),
                Environment.GetEnvironmentVariable("ProgramFiles(x86)"),
                Environment.GetEnvironmentVariable("LOCALAPPDATA"),
            };
            var relatives = new[]
            {
                @"Google\Chrome\Application\chrome.exe",
                @"Microsoft\Edge\Application\msedge.exe",
                @"Mozilla Firefox\firefox.exe",
            };
            foreach (var relative in relatives)
            {
                foreach (var root in roots)
                {
                    if (string.IsNullOrEmpty(root)) continue;
                    string path = Path.Combine(root, relative);
                    if (File.Exists(path)) return path;
                }
            }
            foreach (var exe in new[] { "chrome.exe", "msedge.exe", "firefox.exe" })
            {
                foreach (var hive in new[] { "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER" })
                {
                    try
                    {
                        var path = Microsoft.Win32.Registry.GetValue(
                            hive + @"\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + exe, "", null) as string;
                        if (!string.IsNullOrEmpty(path) && File.Exists(path.Trim('"'))) return path.Trim('"');
                    }
                    catch (Exception)
                    {
                        // Kayıt defterine erişilemezse sıradakine geçilir.
                    }
                }
            }
            return null;
        }

        static void OpenBrowser(string url)
        {
            try
            {
                string browser = FindModernBrowser();
                if (browser != null)
                {
                    Process.Start(browser, url);
                    return;
                }
                Console.WriteLine("  !  Chrome, Edge veya Firefox bulunamadi; varsayilan tarayici aciliyor.");
                Console.WriteLine("     Sayfa bos ya da hatali gorunurse adresi Chrome/Edge/Firefox ile acin:");
                Console.WriteLine("     " + url);
                Process.Start(url);
            }
            catch (Exception)
            {
                Console.WriteLine("  !  Tarayici acilamadi. Adresi elle acin: " + url);
            }
        }
    }
}

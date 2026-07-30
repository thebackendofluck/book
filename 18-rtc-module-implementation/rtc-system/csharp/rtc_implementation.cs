// Full Unity RTC Integration Package
using System;
using System.Collections;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;
using Newtonsoft.Json;
using System.Security.Cryptography;
using System.Text;

namespace CasinoRTC.Unity
{
    /// <summary>
    /// Main RTC Manager for Unity-based casino games
    /// </summary>
    [RequireComponent(typeof(RTCWebSocketClient))]
    public class RTCManager : MonoBehaviour
    {
        #region Singleton
        private static RTCManager _instance;
        public static RTCManager Instance
        {
            get
            {
                if (_instance == null)
                {
                    _instance = FindObjectOfType<RTCManager>();
                    if (_instance == null)
                    {
                        GameObject go = new GameObject("RTCManager");
                        _instance = go.AddComponent<RTCManager>();
                        DontDestroyOnLoad(go);
                    }
                }
                return _instance;
            }
        }
        #endregion

        #region Configuration
        [Header("RTC Service Configuration")]
        [SerializeField] private string rtcServiceUrl = "https://rtc.casino.com/api/v1";
        [SerializeField] private string apiKey = "";
        // Shared HMAC-SHA256 secret used to verify timestamp signatures, matching
        // the Go RTC service's signTimestamp scheme. Inject this via secure build
        // configuration (not source control / hardcoded values) since it is
        // extractable from any shipped client build.
        [SerializeField] private string hmacSecretKey = "";
        [SerializeField] private float syncInterval = 1.0f;
        [SerializeField] private int maxRetries = 3;
        [SerializeField] private float retryDelay = 1.0f;

        [Header("Performance Settings")]
        [SerializeField] private bool useWebSocket = true;
        [SerializeField] private bool cacheTimestamps = true;
        [SerializeField] private int cacheSize = 100;

        [Header("Debug")]
        [SerializeField] private bool debugMode = false;
        [SerializeField] private bool logAllTimestamps = false;
        #endregion

        #region Properties
        public RTCTimestamp LastTimestamp { get; private set; }
        public bool IsConnected { get; private set; }
        public float CurrentDrift { get; private set; }
        public float AverageDrift { get; private set; }
        public float SystemLatency { get; private set; }
        #endregion

        #region Events
        public event Action<RTCTimestamp> OnTimestampReceived;
        public event Action<float> OnDriftDetected;
        public event Action<RTCError> OnError;
        public event Action OnConnected;
        public event Action OnDisconnected;
        #endregion

        #region Private Fields
        private Queue<RTCTimestamp> _timestampBuffer = new Queue<RTCTimestamp>();
        private Dictionary<string, RTCTimestamp> _timestampCache = new Dictionary<string, RTCTimestamp>();
        private RTCWebSocketClient _webSocketClient;
        private Coroutine _syncCoroutine;
        private List<float> _driftHistory = new List<float>();
        private DateTime _lastSyncTime;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            if (_instance != null && _instance != this)
            {
                Destroy(gameObject);
                return;
            }

            _instance = this;
            DontDestroyOnLoad(gameObject);

            _webSocketClient = GetComponent<RTCWebSocketClient>();
            if (_webSocketClient == null)
            {
                _webSocketClient = gameObject.AddComponent<RTCWebSocketClient>();
            }
        }

        private void Start()
        {
            InitializeRTC();
        }

        private void OnDestroy()
        {
            StopSync();
            if (_webSocketClient != null)
            {
                _webSocketClient.Disconnect();
            }
        }

        private void OnApplicationPause(bool pauseStatus)
        {
            if (pauseStatus)
            {
                StopSync();
            }
            else
            {
                StartSync();
            }
        }

        private void OnApplicationFocus(bool hasFocus)
        {
            if (!hasFocus)
            {
                StopSync();
            }
            else
            {
                StartSync();
            }
        }
        #endregion

        #region Initialization
        private void InitializeRTC()
        {
            if (string.IsNullOrEmpty(apiKey))
            {
                Debug.LogError("RTC API Key is not configured!");
                OnError?.Invoke(new RTCError { Code = "CONFIG_ERROR", Message = "API Key missing" });
                return;
            }

            if (useWebSocket)
            {
                InitializeWebSocket();
            }

            StartSync();
        }

        private void InitializeWebSocket()
        {
            _webSocketClient.OnMessageReceived += HandleWebSocketMessage;
            _webSocketClient.OnConnected += () =>
            {
                IsConnected = true;
                OnConnected?.Invoke();
            };
            _webSocketClient.OnDisconnected += () =>
            {
                IsConnected = false;
                OnDisconnected?.Invoke();
            };

            string wsUrl = rtcServiceUrl.Replace("https://", "wss://").Replace("http://", "ws://");
            _webSocketClient.Connect(wsUrl + "/timestamp/stream", apiKey);
        }
        #endregion

        #region Synchronization
        public void StartSync()
        {
            if (_syncCoroutine != null)
            {
                StopCoroutine(_syncCoroutine);
            }
            _syncCoroutine = StartCoroutine(SyncTimeRoutine());
        }

        public void StopSync()
        {
            if (_syncCoroutine != null)
            {
                StopCoroutine(_syncCoroutine);
                _syncCoroutine = null;
            }
        }

        private IEnumerator SyncTimeRoutine()
        {
            while (true)
            {
                if (!useWebSocket || !IsConnected)
                {
                    yield return FetchTimestampCoroutine();
                }

                yield return new WaitForSeconds(syncInterval);
            }
        }

        private IEnumerator FetchTimestampCoroutine()
        {
            yield return FetchTimestamp((timestamp, error) =>
            {
                if (error != null)
                {
                    HandleError(error);
                }
                else if (timestamp != null)
                {
                    ProcessTimestamp(timestamp);
                }
            });
        }
        #endregion

        #region Timestamp Operations
        public void GetTimestamp(Action<RTCTimestamp, RTCError> callback)
        {
            StartCoroutine(FetchTimestamp(callback));
        }

        private IEnumerator FetchTimestamp(Action<RTCTimestamp, RTCError> callback)
        {
            int attempts = 0;
            RTCError lastError = null;

            while (attempts < maxRetries)
            {
                using (UnityWebRequest request = UnityWebRequest.Get($"{rtcServiceUrl}/timestamp"))
                {
                    request.SetRequestHeader("X-API-Key", apiKey);
                    request.SetRequestHeader("X-Request-ID", Guid.NewGuid().ToString());
                    request.SetRequestHeader("X-Game-ID", Application.productName);
                    request.SetRequestHeader("X-Platform", Application.platform.ToString());

                    DateTime requestStart = DateTime.UtcNow;
                    yield return request.SendWebRequest();
                    SystemLatency = (float)(DateTime.UtcNow - requestStart).TotalMilliseconds;

                    if (request.result == UnityWebRequest.Result.Success)
                    {
                        try
                        {
                            RTCTimestamp timestamp = JsonConvert.DeserializeObject<RTCTimestamp>(
                                request.downloadHandler.text
                            );

                            if (ValidateTimestamp(timestamp))
                            {
                                callback?.Invoke(timestamp, null);
                                yield break;
                            }
                            else
                            {
                                lastError = new RTCError
                                {
                                    Code = "VALIDATION_ERROR",
                                    Message = "Timestamp validation failed"
                                };
                            }
                        }
                        catch (Exception e)
                        {
                            lastError = new RTCError
                            {
                                Code = "PARSE_ERROR",
                                Message = e.Message
                            };
                        }
                    }
                    else
                    {
                        lastError = new RTCError
                        {
                            Code = request.responseCode.ToString(),
                            Message = request.error
                        };
                    }
                }

                attempts++;
                if (attempts < maxRetries)
                {
                    yield return new WaitForSeconds(retryDelay * attempts);
                }
            }

            callback?.Invoke(null, lastError);
        }

        public async Task<RTCTimestamp> GetTimestampAsync()
        {
            TaskCompletionSource<RTCTimestamp> tcs = new TaskCompletionSource<RTCTimestamp>();

            GetTimestamp((timestamp, error) =>
            {
                if (error != null)
                {
                    tcs.SetException(new Exception(error.Message));
                }
                else
                {
                    tcs.SetResult(timestamp);
                }
            });

            return await tcs.Task;
        }
        #endregion

        #region Processing
        private void ProcessTimestamp(RTCTimestamp timestamp)
        {
            LastTimestamp = timestamp;
            _lastSyncTime = DateTime.UtcNow;

            // Update buffer
            _timestampBuffer.Enqueue(timestamp);
            if (_timestampBuffer.Count > cacheSize)
            {
                _timestampBuffer.Dequeue();
            }

            // Update cache
            if (cacheTimestamps && !string.IsNullOrEmpty(timestamp.metadata?.GetValueOrDefault("request_id")))
            {
                _timestampCache[timestamp.metadata["request_id"]] = timestamp;
                if (_timestampCache.Count > cacheSize)
                {
                    // Remove oldest entry
                    var oldestKey = "";
                    long oldestTime = long.MaxValue;
                    foreach (var kvp in _timestampCache)
                    {
                        if (kvp.Value.unix < oldestTime)
                        {
                            oldestTime = kvp.Value.unix;
                            oldestKey = kvp.Key;
                        }
                    }
                    if (!string.IsNullOrEmpty(oldestKey))
                    {
                        _timestampCache.Remove(oldestKey);
                    }
                }
            }

            // Update drift metrics
            CurrentDrift = (float)timestamp.drift_ms;
            _driftHistory.Add(CurrentDrift);
            if (_driftHistory.Count > 100)
            {
                _driftHistory.RemoveAt(0);
            }
            AverageDrift = CalculateAverageDrift();

            // Check for anomalies
            if (Math.Abs(CurrentDrift) > 10)
            {
                OnDriftDetected?.Invoke(CurrentDrift);
            }

            // Invoke event
            OnTimestampReceived?.Invoke(timestamp);

            if (debugMode)
            {
                Debug.Log($"RTC Timestamp: {timestamp.iso8601} | Drift: {CurrentDrift}ms | Confidence: {timestamp.confidence}");
            }
        }

        private float CalculateAverageDrift()
        {
            if (_driftHistory.Count == 0) return 0;

            float sum = 0;
            foreach (float drift in _driftHistory)
            {
                sum += drift;
            }
            return sum / _driftHistory.Count;
        }

        private bool ValidateTimestamp(RTCTimestamp timestamp)
        {
            // Basic validation
            if (timestamp == null) return false;
            if (string.IsNullOrEmpty(timestamp.signature)) return false;
            if (timestamp.unix <= 0) return false;

            // Check if timestamp is reasonably recent
            DateTime tsTime = DateTimeOffset.FromUnixTimeSeconds(timestamp.unix).UtcDateTime;
            if (Math.Abs((DateTime.UtcNow - tsTime).TotalSeconds) > 300) // 5 minutes
            {
                return false;
            }

            // Verify the HMAC-SHA256 signature against the shared secret, matching
            // the Go RTC service's signTimestamp scheme ("unix:nano:source").
            if (string.IsNullOrEmpty(hmacSecretKey))
            {
                Debug.LogError("RTC HMAC secret key is not configured; rejecting timestamp.");
                return false;
            }

            string data = $"{timestamp.unix}:{timestamp.nano}:{timestamp.source}";
            string expectedSignature = ComputeHmacSha256(data, hmacSecretKey);

            return FixedTimeEquals(expectedSignature, timestamp.signature);
        }

        private static string ComputeHmacSha256(string data, string key)
        {
            using (var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(key)))
            {
                byte[] hash = hmac.ComputeHash(Encoding.UTF8.GetBytes(data));
                var sb = new StringBuilder(hash.Length * 2);
                foreach (byte b in hash)
                {
                    sb.Append(b.ToString("x2"));
                }
                return sb.ToString();
            }
        }

        /// <summary>
        /// Constant-time comparison for hex-encoded signatures, to avoid
        /// leaking timing information about how much of the signature matched.
        /// </summary>
        private static bool FixedTimeEquals(string expected, string actual)
        {
            string a = expected.ToLowerInvariant();
            string b = actual.ToLowerInvariant();

            int diff = a.Length ^ b.Length;
            for (int i = 0; i < a.Length && i < b.Length; i++)
            {
                diff |= a[i] ^ b[i];
            }
            return diff == 0;
        }
        #endregion

        #region WebSocket Handling
        private void HandleWebSocketMessage(string message)
        {
            try
            {
                RTCTimestamp timestamp = JsonConvert.DeserializeObject<RTCTimestamp>(message);
                ProcessTimestamp(timestamp);
            }
            catch (Exception e)
            {
                HandleError(new RTCError
                {
                    Code = "WS_PARSE_ERROR",
                    Message = e.Message
                });
            }
        }
        #endregion

        #region Error Handling
        private void HandleError(RTCError error)
        {
            if (debugMode)
            {
                Debug.LogError($"RTC Error [{error.Code}]: {error.Message}");
            }

            OnError?.Invoke(error);

            // Implement fallback strategies
            switch (error.Code)
            {
                case "503":
                case "SERVICE_UNAVAILABLE":
                    // Try to use cached timestamp
                    if (_timestampBuffer.Count > 0)
                    {
                        var cached = _timestampBuffer.ToArray()[_timestampBuffer.Count - 1];
                        // Extrapolate based on elapsed time
                        long elapsedMs = (long)(DateTime.UtcNow - _lastSyncTime).TotalMilliseconds;
                        cached.unix += elapsedMs / 1000;
                        cached.nano += elapsedMs * 1000000;
                        ProcessTimestamp(cached);
                    }
                    break;
            }
        }
        #endregion

        #region Game Event Recording
        public IEnumerator RecordGameEvent(string eventType, Dictionary<string, object> eventData)
        {
            // Get fresh timestamp
            RTCTimestamp timestamp = null;
            RTCError error = null;

            yield return FetchTimestamp((ts, err) =>
            {
                timestamp = ts;
                error = err;
            });

            if (error != null)
            {
                Debug.LogError($"Failed to get timestamp for event: {error.Message}");
                yield break;
            }

            // Add RTC data to event
            eventData["rtc_timestamp"] = timestamp.iso8601;
            eventData["rtc_unix"] = timestamp.unix;
            eventData["rtc_nano"] = timestamp.nano;
            eventData["rtc_signature"] = timestamp.signature;
            eventData["rtc_confidence"] = timestamp.confidence;
            eventData["rtc_drift_ms"] = timestamp.drift_ms;

            // Send to server
            yield return SendEventToServer(eventType, eventData);
        }

        private IEnumerator SendEventToServer(string eventType, Dictionary<string, object> eventData)
        {
            var payload = new
            {
                event_type = eventType,
                event_data = eventData,
                client_timestamp = DateTime.UtcNow.ToString("O"),
                session_id = GetSessionId(),
                platform = Application.platform.ToString(),
                game_version = Application.version
            };

            string json = JsonConvert.SerializeObject(payload);
            byte[] bodyRaw = Encoding.UTF8.GetBytes(json);

            using (UnityWebRequest request = new UnityWebRequest($"{rtcServiceUrl}/events", "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(bodyRaw);
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.SetRequestHeader("X-API-Key", apiKey);

                yield return request.SendWebRequest();

                if (request.result != UnityWebRequest.Result.Success)
                {
                    Debug.LogError($"Failed to record event: {request.error}");
                }
            }
        }

        private string GetSessionId()
        {
            // Implement session ID generation/retrieval
            return SystemInfo.deviceUniqueIdentifier;
        }
        #endregion
    }

    /// <summary>
    /// WebSocket client for real-time timestamp streaming
    /// </summary>
    public class RTCWebSocketClient : MonoBehaviour
    {
        public event Action<string> OnMessageReceived;
        public event Action OnConnected;
        public event Action OnDisconnected;
        public event Action<string> OnError;

        private WebSocket.WebSocket _webSocket;
        private Queue<string> _messageQueue = new Queue<string>();
        private bool _isConnected = false;

        public void Connect(string url, string apiKey)
        {
            if (_webSocket != null)
            {
                Disconnect();
            }

            _webSocket = new WebSocket.WebSocket(url);
            _webSocket.Headers.Add("X-API-Key", apiKey);

            _webSocket.OnOpen += OnWebSocketOpen;
            _webSocket.OnMessage += OnWebSocketMessage;
            _webSocket.OnError += OnWebSocketError;
            _webSocket.OnClose += OnWebSocketClose;

            _webSocket.Connect();
        }

        public void Disconnect()
        {
            if (_webSocket != null)
            {
                _webSocket.Close();
                _webSocket = null;
            }
        }

        private void Update()
        {
            // Process queued messages on main thread
            lock (_messageQueue)
            {
                while (_messageQueue.Count > 0)
                {
                    string message = _messageQueue.Dequeue();
                    OnMessageReceived?.Invoke(message);
                }
            }
        }

        private void OnWebSocketOpen()
        {
            _isConnected = true;
            OnConnected?.Invoke();
        }

        private void OnWebSocketMessage(byte[] data)
        {
            string message = Encoding.UTF8.GetString(data);
            lock (_messageQueue)
            {
                _messageQueue.Enqueue(message);
            }
        }

        private void OnWebSocketError(string error)
        {
            OnError?.Invoke(error);
        }

        private void OnWebSocketClose()
        {
            _isConnected = false;
            OnDisconnected?.Invoke();
        }

        private void OnDestroy()
        {
            Disconnect();
        }
    }

    /// <summary>
    /// RTC Timestamp data structure
    /// </summary>
    [Serializable]
    public class RTCTimestamp
    {
        public long unix { get; set; }
        public long nano { get; set; }
        public string iso8601 { get; set; }
        public string signature { get; set; }
        public double confidence { get; set; }
        public double drift_ms { get; set; }
        public string source { get; set; }
        public double temperature { get; set; }
        public double battery_level { get; set; }
        public Dictionary<string, string> metadata { get; set; }
    }

    /// <summary>
    /// RTC Error structure
    /// </summary>
    [Serializable]
    public class RTCError
    {
        public string Code { get; set; }
        public string Message { get; set; }
        public Dictionary<string, object> Details { get; set; }
    }
}

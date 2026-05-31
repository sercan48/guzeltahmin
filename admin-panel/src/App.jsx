import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  LayoutDashboard, 
  Users, 
  Settings, 
  AlertTriangle, 
  Activity, 
  RefreshCw, 
  Send, 
  Trash2, 
  UserPlus, 
  Calendar, 
  Clock, 
  Cpu, 
  Sliders, 
  Search, 
  CheckCircle,
  Database,
  Info,
  Shield,
  DownloadCloud,
  FileText,
  Map,
  Plus
} from 'lucide-react';

const API_BASE = 'http://localhost:8000/api';

function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState({ text: '', type: '' });
  
  // Dashboard states
  const [stats, setStats] = useState({
    total_users: 0,
    premium_users: 0,
    vip_users: 0,
    yesterday_accuracy: 0.0,
    yesterday_roi: 0.0,
    yesterday_correct: 0,
    yesterday_total: 0,
    weekly_accuracy: 0.0,
    weekly_roi: 0.0,
  });
  const [activities, setActivities] = useState([]);
  const [sourceLogs, setSourceLogs] = useState([]);
  const [dbPath, setDbPath] = useState('');
  const [backups, setBackups] = useState([]);

  // Config states
  const [config, setConfig] = useState({
    temperature: 1.15,
    live_betting_enabled: false,
    self_learning_enabled: true
  });

  // Subscribers states
  const [subscribers, setSubscribers] = useState([]);
  const [searchSub, setSearchSub] = useState('');
  const [automationLog, setAutomationLog] = useState('');
  const [newSub, setNewSub] = useState({
    telegram_id: '',
    username: '',
    full_name: '',
    plan: 'free',
    is_active: 1,
    end_date: ''
  });

  // Predictions states
  const [predictions, setPredictions] = useState([]);

  // Injury Editor states
  const [teams, setTeams] = useState([]);
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const [teamStatus, setTeamStatus] = useState({
    team_id: '',
    injured_players: [],
    suspended_players: [],
    injury_count: 0,
    suspension_count: 0,
    squad_value_eur: 0,
    key_absences: [],
    power_loss_pct: 0.0
  });
  const [newInjury, setNewInjury] = useState({ name: '', injury: '', severity: 'medium', return_date: '' });
  const [newSuspension, setNewSuspension] = useState({ name: '', reason: '', matches_remaining: 1 });

  // ── Initial Fetching ───────────────────────────────────────────────
  useEffect(() => {
    fetchDashboardData();
    fetchConfig();
    fetchSubscribers();
    fetchPredictions();
    fetchTeams();
    fetchBackups();
  }, []);

  const showMessage = (text, type = 'success') => {
    setMessage({ text, type });
    setTimeout(() => setMessage({ text: '', type: '' }), 5000);
  };

  const fetchDashboardData = async () => {
    try {
      const res = await axios.get(`${API_BASE}/dashboard`);
      setStats(res.data.stats);
      setActivities(res.data.recent_activity || []);
      setSourceLogs(res.data.source_logs || []);
      setDbPath(res.data.database_path || '');
    } catch (err) {
      console.error(err);
      showMessage('Dashboard verileri yüklenemedi', 'error');
    }
  };

  const fetchConfig = async () => {
    try {
      const res = await axios.get(`${API_BASE}/config`);
      setConfig(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchSubscribers = async () => {
    try {
      const res = await axios.get(`${API_BASE}/subscribers`);
      setSubscribers(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchPredictions = async () => {
    try {
      const res = await axios.get(`${API_BASE}/predictions`);
      setPredictions(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchTeams = async () => {
    try {
      const res = await axios.get(`${API_BASE}/teams`);
      setTeams(res.data);
      if (res.data.length > 0) {
        setSelectedTeamId(res.data[0].id);
        fetchTeamStatus(res.data[0].id);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchTeamStatus = async (teamId) => {
    if (!teamId) return;
    try {
      const res = await axios.get(`${API_BASE}/team_status/${teamId}`);
      setTeamStatus(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchBackups = async () => {
    try {
      const res = await axios.get(`${API_BASE}/database/backups`);
      setBackups(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const handleTeamChange = (e) => {
    const id = e.target.value;
    setSelectedTeamId(id);
    fetchTeamStatus(id);
  };

  // ── Actions & Triggers ─────────────────────────────────────────────
  const triggerScript = async (endpoint, name) => {
    setLoading(true);
    try {
      showMessage(`${name} tetiklendi, arka planda çalışıyor...`, 'info');
      await axios.post(`${API_BASE}/trigger/${endpoint}`);
      setTimeout(fetchDashboardData, 3000);
    } catch (err) {
      showMessage(`${name} çalıştırılamadı`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const triggerDbSync = async () => {
    setLoading(true);
    try {
      showMessage('Güncel maç sonuçları ve takvim verisi çekiliyor (DB Sync başlatıldı)...', 'info');
      await axios.post(`${API_BASE}/trigger/db_sync`);
      setTimeout(fetchDashboardData, 3000);
    } catch (err) {
      showMessage('Veri güncellemesi tetiklenemedi', 'error');
    } finally {
      setLoading(false);
    }
  };

  const createDatabaseBackup = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/database/backup`);
      showMessage(`SQLite veritabanı yedeği alındı: ${res.data.filename}`);
      fetchBackups();
    } catch (err) {
      showMessage('Veritabanı yedeği oluşturulamadı', 'error');
    } finally {
      setLoading(false);
    }
  };

  const checkSubscriberExpirations = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/subscribers/check_expirations`);
      const kickedCount = res.data.kicked_count;
      const kickedList = res.data.kicked_users;
      
      if (kickedCount > 0) {
        const names = kickedList.map(u => `@${u.username || u.telegram_id}`).join(', ');
        setAutomationLog(`[${new Date().toLocaleTimeString()}] Otomasyon çalıştı: Süresi dolan ${kickedCount} üye gruptan çıkarıldı: ${names}`);
        showMessage(`Abonelik denetimi tamamlandı! Süresi dolan ${kickedCount} üyenin yetkisi kaldırıldı.`);
      } else {
        setAutomationLog(`[${new Date().toLocaleTimeString()}] Otomasyon çalıştı: Süresi dolan herhangi bir üye bulunamadı.`);
        showMessage('Abonelik denetimi tamamlandı. Tüm üyelerin abonelik süreleri aktif.');
      }
      fetchSubscribers();
      fetchDashboardData();
    } catch (err) {
      showMessage('Üye otomasyon denetimi başarısız oldu', 'error');
    } finally {
      setLoading(false);
    }
  };

  const sendTelegramReport = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/bot/send_report`);
      showMessage('Performans raporu Telegram kanalına başarıyla gönderildi!');
    } catch (err) {
      showMessage('Rapor gönderilemedi. Telegram token veya Kanal ID ayarlarını kontrol edin.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const saveConfig = async (updatedConfig) => {
    try {
      const res = await axios.post(`${API_BASE}/config`, updatedConfig);
      setConfig(res.data.config);
      showMessage('Ayarlar başarıyla kaydedildi');
    } catch (err) {
      showMessage('Ayarlar kaydedilemedi', 'error');
    }
  };

  // ── Subscriber CRUD ───────────────────────────────────────────────
  const addSubscriber = async (e) => {
    e.preventDefault();
    if (!newSub.telegram_id) {
      showMessage('Telegram ID zorunludur', 'error');
      return;
    }
    try {
      await axios.post(`${API_BASE}/subscribers`, newSub);
      showMessage('Üye başarıyla eklendi');
      setNewSub({
        telegram_id: '',
        username: '',
        full_name: '',
        plan: 'free',
        is_active: 1,
        end_date: ''
      });
      fetchSubscribers();
      fetchDashboardData();
    } catch (err) {
      showMessage(err.response?.data?.detail || 'Üye eklenemedi', 'error');
    }
  };

  const deleteSubscriber = async (tgId) => {
    if (!window.confirm('Bu üyeyi silmek istediğinize emin misiniz?')) return;
    try {
      await axios.delete(`${API_BASE}/subscribers/${tgId}`);
      showMessage('Üye silindi');
      fetchSubscribers();
      fetchDashboardData();
    } catch (err) {
      showMessage('Üye silinemedi', 'error');
    }
  };

  const quickExtendSubscription = async (tgId, days) => {
    const sub = subscribers.find(s => s.telegram_id === tgId);
    if (!sub) return;

    let targetDate = new Date();
    if (sub.end_date) {
      targetDate = new Date(sub.end_date);
      if (targetDate < new Date()) {
        targetDate = new Date();
      }
    }
    targetDate.setDate(targetDate.getDate() + days);
    const dateStr = targetDate.toISOString().slice(0, 19).replace('T', ' ');

    try {
      await axios.put(`${API_BASE}/subscribers/${tgId}`, {
        ...sub,
        end_date: dateStr,
        is_active: 1
      });
      showMessage(`Abonelik +${days} gün uzatıldı!`);
      fetchSubscribers();
      fetchDashboardData();
    } catch (err) {
      showMessage('Abonelik uzatılamadı', 'error');
    }
  };

  // ── AI Insight Editor Actions ─────────────────────────────────────
  const addInjury = () => {
    if (!newInjury.name) return;
    const list = [...teamStatus.injured_players, newInjury];
    setTeamStatus({
      ...teamStatus,
      injured_players: list,
      injury_count: list.length
    });
    setNewInjury({ name: '', injury: '', severity: 'medium', return_date: '' });
  };

  const removeInjury = (index) => {
    const list = [...teamStatus.injured_players];
    list.splice(index, 1);
    setTeamStatus({
      ...teamStatus,
      injured_players: list,
      injury_count: list.length
    });
  };

  const addSuspension = () => {
    if (!newSuspension.name) return;
    const list = [...teamStatus.suspended_players, newSuspension];
    setTeamStatus({
      ...teamStatus,
      suspended_players: list,
      suspension_count: list.length
    });
    setNewSuspension({ name: '', reason: '', matches_remaining: 1 });
  };

  const removeSuspension = (index) => {
    const list = [...teamStatus.suspended_players];
    list.splice(index, 1);
    setTeamStatus({
      ...teamStatus,
      suspended_players: list,
      suspension_count: list.length
    });
  };

  const addKeyAbsence = (name) => {
    if (!name || teamStatus.key_absences.includes(name)) return;
    setTeamStatus({
      ...teamStatus,
      key_absences: [...teamStatus.key_absences, name]
    });
  };

  const removeKeyAbsence = (index) => {
    const list = [...teamStatus.key_absences];
    list.splice(index, 1);
    setTeamStatus({
      ...teamStatus,
      key_absences: list
    });
  };

  const saveTeamInjuryStatus = async () => {
    try {
      await axios.post(`${API_BASE}/team_status/${selectedTeamId}`, teamStatus);
      showMessage('Sakatlık ve ceza durumu başarıyla SQLite veritabanına kaydedildi!');
      fetchTeamStatus(selectedTeamId);
    } catch (err) {
      showMessage('Kayıt başarısız oldu', 'error');
    }
  };

  const filteredSubs = subscribers.filter(s => 
    s.telegram_id.toString().includes(searchSub) ||
    (s.username && s.username.toLowerCase().includes(searchSub.toLowerCase())) ||
    (s.full_name && s.full_name.toLowerCase().includes(searchSub.toLowerCase()))
  );

  // Helper function to render status label for subscribers
  const getSubStatusBadge = (sub) => {
    if (!sub.is_active) {
      return (
        <span className="px-2 py-0.5 rounded text-xxs font-bold bg-red-500/10 text-red-400 border border-red-500/20">
          Süresi Doldu
        </span>
      );
    }
    
    if (sub.end_date) {
      const daysLeft = Math.ceil((new Date(sub.end_date) - new Date()) / (1000 * 60 * 60 * 24));
      if (daysLeft <= 0) {
        return (
          <span className="px-2 py-0.5 rounded text-xxs font-bold bg-red-500/10 text-red-400 border border-red-500/20">
            Süresi Doldu
          </span>
        );
      }
      if (daysLeft <= 3) {
        return (
          <span className="px-2 py-0.5 rounded text-xxs font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
            Son {daysLeft} Gün
          </span>
        );
      }
    }
    
    return (
      <span className="px-2 py-0.5 rounded text-xxs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
        Aktif
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-[#0b0c10] text-[#c5c6c7] font-sans antialiased flex">
      {/* Sidebar Navigation */}
      <aside className="w-64 bg-[#1f2833]/80 border-r border-[#2e303a] p-6 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-lg bg-teal-500/20 flex items-center justify-center border border-teal-500/30">
              <Cpu className="w-6 h-6 text-teal-400" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-white tracking-tight leading-none">Güzel Tahmin</h1>
              <span className="text-xs text-teal-500 font-mono">QUANT ADMIN V4.1</span>
            </div>
          </div>

          <nav className="space-y-1">
            <button 
              onClick={() => setActiveTab('dashboard')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all ${activeTab === 'dashboard' ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'text-gray-400 hover:text-white hover:bg-white/5 border border-transparent'}`}
            >
              <LayoutDashboard className="w-4 h-4" />
              Kontrol Paneli
            </button>
            <button 
              onClick={() => setActiveTab('subscribers')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all ${activeTab === 'subscribers' ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'text-gray-400 hover:text-white hover:bg-white/5 border border-transparent'}`}
            >
              <Users className="w-4 h-4" />
              Üye Otomasyonu
            </button>
            <button 
              onClick={() => setActiveTab('predictions')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all ${activeTab === 'predictions' ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'text-gray-400 hover:text-white hover:bg-white/5 border border-transparent'}`}
            >
              <Sliders className="w-4 h-4" />
              Model & Tahmin Ayarı
            </button>
            <button 
              onClick={() => setActiveTab('injuries')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all ${activeTab === 'injuries' ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'text-gray-400 hover:text-white hover:bg-white/5 border border-transparent'}`}
            >
              <AlertTriangle className="w-4 h-4" />
              AI Insight (Sakatlık)
            </button>
            <button 
              onClick={() => setActiveTab('leagues')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all ${activeTab === 'leagues' ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'text-gray-400 hover:text-white hover:bg-white/5 border border-transparent'}`}
            >
              <Map className="w-4 h-4" />
              Lig & Algoritmalar
            </button>
          </nav>
        </div>

        <div className="rounded-lg bg-black/40 border border-[#2e303a] p-4 font-mono text-xxs text-gray-500">
          <div className="flex items-center gap-2 mb-1.5 text-gray-400">
            <Database className="w-3.5 h-3.5 text-teal-500" />
            <span>SQLite Active</span>
          </div>
          <p className="truncate" title={dbPath}>{dbPath.split(/[\\/]/).pop()}</p>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-h-screen">
        {/* Header Status Bar */}
        <header className="border-b border-[#2e303a] px-8 py-4 bg-[#1f2833]/30 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-mono text-gray-400">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>API SERVER RUNNING: localhost:8000</span>
          </div>
          
          {loading && (
            <div className="flex items-center gap-2 text-teal-400 text-xs font-mono">
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>İşlem Yürütülüyor...</span>
            </div>
          )}
        </header>

        <div className="flex-1 p-8 space-y-6 overflow-y-auto max-w-7xl w-full mx-auto">
          {/* Notification Messages */}
          {message.text && (
            <div className={`p-4 rounded-lg flex items-center gap-3 border text-sm font-medium ${
              message.type === 'error' ? 'bg-red-500/10 border-red-500/20 text-red-400' : 
              message.type === 'info' ? 'bg-blue-500/10 border-blue-500/20 text-blue-400' : 
              'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
            }`}>
              <Info className="w-4 h-4 shrink-0" />
              <span>{message.text}</span>
            </div>
          )}

          {/* TAB 1: Kontrol Paneli */}
          {activeTab === 'dashboard' && (
            <div className="space-y-6">
              {/* Quant Metrics Grid */}
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="glass-card rounded-xl p-5">
                  <span className="text-xs font-mono text-gray-500 block mb-1">DÜNÜN TAHMİN İSABETİ</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-3xl font-bold text-white">%{stats.yesterday_accuracy}</span>
                    <span className="text-xs text-teal-400 font-mono">({stats.yesterday_correct}/{stats.yesterday_total})</span>
                  </div>
                </div>
                <div className="glass-card rounded-xl p-5">
                  <span className="text-xs font-mono text-gray-500 block mb-1">DÜNÜN ROI (FLAT STAKE)</span>
                  <span className={`text-3xl font-bold ${stats.yesterday_roi >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {stats.yesterday_roi >= 0 ? '+' : ''}%{stats.yesterday_roi}
                  </span>
                </div>
                <div className="glass-card rounded-xl p-5">
                  <span className="text-xs font-mono text-gray-500 block mb-1">HAFTALIK GENEL İSABET</span>
                  <span className="text-3xl font-bold text-white">%{stats.weekly_accuracy}</span>
                </div>
                <div className="glass-card rounded-xl p-5">
                  <span className="text-xs font-mono text-gray-500 block mb-1">AKTİF VIP / PREM ABONE</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-3xl font-bold text-white">{stats.vip_users + stats.premium_users}</span>
                    <span className="text-xs text-gray-500 font-mono">/ {stats.total_users} toplam</span>
                  </div>
                </div>
              </div>

              {/* Action Buttons Panel */}
              <div className="glass-panel rounded-xl p-6">
                <h3 className="text-sm font-semibold text-white font-mono mb-4 flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-teal-400" />
                  SİSTEM TETİKLEMEK VE MAÇ TAHMİN MOTORU
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                  <button 
                    onClick={() => triggerScript('predict_today', "Günün Maçlarını Tahmin Et")}
                    className="flex items-center justify-center gap-2.5 px-4 py-3 bg-teal-500 hover:bg-teal-600 text-black font-semibold rounded-lg text-sm transition-all"
                  >
                    <RefreshCw className="w-4 h-4" />
                    Bugünü Tahmin Et
                  </button>
                  <button 
                    onClick={() => triggerScript('run_production', "Production Pipeline")}
                    className="flex items-center justify-center gap-2.5 px-4 py-3 bg-[#1f2833] hover:bg-white/5 border border-[#2e303a] text-white font-semibold rounded-lg text-sm transition-all"
                  >
                    <Cpu className="w-4 h-4 text-teal-400" />
                    Full Pipeline Çalıştır
                  </button>
                  <button 
                    onClick={triggerDbSync}
                    className="flex items-center justify-center gap-2.5 px-4 py-3 bg-emerald-600/10 hover:bg-emerald-600/20 border border-emerald-500/30 text-emerald-400 font-semibold rounded-lg text-sm transition-all"
                  >
                    <DownloadCloud className="w-4 h-4" />
                    Güncel Verileri Çek (Sync)
                  </button>
                  <button 
                    onClick={sendTelegramReport}
                    className="flex items-center justify-center gap-2.5 px-4 py-3 bg-sky-500/10 hover:bg-sky-500/20 border border-sky-500/30 text-sky-400 font-semibold rounded-lg text-sm transition-all"
                  >
                    <Send className="w-4 h-4" />
                    Telegram Raporu Gönder
                  </button>
                </div>
              </div>

              {/* Database and Backups Row */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {/* Backups Panel */}
                <div className="glass-panel rounded-xl p-6 md:col-span-2">
                  <div className="flex justify-between items-center mb-4">
                    <h3 className="text-sm font-semibold text-white font-mono flex items-center gap-2">
                      <Shield className="w-4 h-4 text-teal-400" />
                      VERİTABANI YEDEKLERİ (BACKUPS)
                    </h3>
                    <button 
                      onClick={createDatabaseBackup}
                      className="px-3 py-1.5 bg-[#1f2833] hover:bg-white/5 border border-[#2e303a] text-white font-semibold rounded-lg text-xs font-mono transition-all"
                    >
                      Yeni Yedek Oluştur
                    </button>
                  </div>
                  
                  <div className="max-h-56 overflow-y-auto pr-1">
                    <table className="w-full text-left font-mono text-xxs">
                      <thead>
                        <tr className="text-gray-500 border-b border-[#2e303a]">
                          <th className="py-2">Yedek Dosyası</th>
                          <th className="py-2">Boyut</th>
                          <th className="py-2">Tarih</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#2e303a] text-gray-300">
                        {backups.length === 0 ? (
                          <tr>
                            <td colSpan="3" className="py-4 text-center text-gray-500">Henüz veritabanı yedeği bulunmuyor.</td>
                          </tr>
                        ) : (
                          backups.map(bk => (
                            <tr key={bk.name} className="hover:bg-white/2">
                              <td className="py-2 text-teal-400 font-bold flex items-center gap-1.5">
                                <FileText className="w-3.5 h-3.5 shrink-0" />
                                {bk.name}
                              </td>
                              <td className="py-2">{bk.size_mb} MB</td>
                              <td className="py-2 text-gray-500">{bk.created_at}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Scraper / API Logs */}
                <div className="glass-panel rounded-xl p-6 md:col-span-1">
                  <h3 className="text-sm font-semibold text-white font-mono mb-4 flex items-center gap-2">
                    <Database className="w-4 h-4 text-teal-400" />
                    APIS SAĞLIK LOGLARI
                  </h3>
                  <div className="space-y-3 max-h-56 overflow-y-auto pr-1">
                    {sourceLogs.length === 0 ? (
                      <p className="text-xs text-gray-500 font-mono">Tetiklenmiş veri logu bulunmuyor.</p>
                    ) : (
                      sourceLogs.map(log => (
                        <div key={log.id} className="border-b border-[#2e303a] pb-2 font-mono text-xs">
                          <div className="flex justify-between text-gray-400 mb-1">
                            <span className="text-white font-bold">{log.source_name}</span>
                            <span className="text-xxs">{log.timestamp.slice(11, 19)}</span>
                          </div>
                          <div className="flex justify-between text-gray-500">
                            <span className={`text-xxs uppercase px-1 rounded ${log.status === 'success' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>{log.status}</span>
                            <span>{log.response_time_ms} ms</span>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: Üye Yönetimi */}
          {activeTab === 'subscribers' && (
            <div className="space-y-6">
              {/* Automation & Info Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6 items-stretch">
                {/* Expiration Automation */}
                <div className="glass-panel rounded-xl p-6 md:col-span-2 flex flex-col justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-white font-mono mb-2 flex items-center gap-2">
                      <Shield className="w-4 h-4 text-teal-400" />
                      ABONELİK SÜRE DENETİMİ & OTOMATİK GRUPTAN ÇIKARMA
                    </h3>
                    <p className="text-xs text-gray-400 mb-4 font-sans leading-relaxed">
                      Sistem, üyelik bitiş tarihi geçen VIP ve Premium kullanıcıları otomatik olarak tespit eder. 
                      Yetkilerini kaldırıp pasife çeker ve entegre Telegram Bot API aracılığıyla ilgili kanaldan çıkartır (Kick).
                    </p>
                  </div>
                  
                  <div className="space-y-3">
                    <button 
                      onClick={checkSubscriberExpirations}
                      className="px-5 py-2.5 bg-amber-500 hover:bg-amber-600 text-black font-bold rounded-lg text-xs font-mono transition-all flex items-center gap-2 w-max"
                    >
                      <RefreshCw className="w-4 h-4" />
                      Süresi Bitenleri Denetle & Gruptan Çıkar
                    </button>
                    
                    {automationLog && (
                      <div className="bg-black/50 border border-[#2e303a] p-3 rounded-lg font-mono text-xxs text-amber-400">
                        {automationLog}
                      </div>
                    )}
                  </div>
                </div>

                {/* Add Member Panel */}
                <div className="glass-panel rounded-xl p-6 md:col-span-1">
                  <h3 className="text-sm font-semibold text-white font-mono mb-4 flex items-center gap-2">
                    <Search className="w-4 h-4 text-teal-400" />
                    ÜYE ARA
                  </h3>
                  <div className="space-y-4">
                    <input 
                      type="text" 
                      placeholder="Telegram ID, isim, kullanıcı adı..."
                      value={searchSub}
                      onChange={(e) => setSearchSub(e.target.value)}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-4 py-2.5 text-xs text-white placeholder-gray-500 font-mono outline-none"
                    />
                    <div className="p-3 bg-teal-500/5 border border-teal-500/10 rounded-lg text-xxs text-gray-400 font-sans">
                      <strong className="text-teal-400 block mb-1">Abonelik Durumu Kriteri:</strong>
                      Ödemesini yapıp yenilenen üyelerin plan ve süre bilgisi güncellenerek grupta aktif kalmaları sağlanır.
                    </div>
                  </div>
                </div>
              </div>

              {/* Add Subscriber Panel */}
              <div className="glass-panel rounded-xl p-6">
                <h3 className="text-sm font-semibold text-white font-mono mb-4 flex items-center gap-2">
                  <UserPlus className="w-4 h-4 text-teal-400" />
                  YENİ ÜYE EKLE / YENİLEME YAP (SQLITE INSERT)
                </h3>
                <form onSubmit={addSubscriber} className="grid grid-cols-1 md:grid-cols-6 gap-4">
                  <div className="md:col-span-1">
                    <label className="text-xxs text-gray-500 font-mono block mb-1">Telegram ID *</label>
                    <input 
                      type="number" 
                      required
                      placeholder="Örn: 987654"
                      value={newSub.telegram_id}
                      onChange={(e) => setNewSub({...newSub, telegram_id: parseInt(e.target.value) || ''})}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-xs text-white font-mono outline-none"
                    />
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xxs text-gray-500 font-mono block mb-1">Kullanıcı Adı</label>
                    <input 
                      type="text" 
                      placeholder="Örn: ahmet1"
                      value={newSub.username}
                      onChange={(e) => setNewSub({...newSub, username: e.target.value})}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-xs text-white font-mono outline-none"
                    />
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xxs text-gray-500 font-mono block mb-1">Tam İsim</label>
                    <input 
                      type="text" 
                      placeholder="Ahmet Yılmaz"
                      value={newSub.full_name}
                      onChange={(e) => setNewSub({...newSub, full_name: e.target.value})}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-xs text-white outline-none"
                    />
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xxs text-gray-500 font-mono block mb-1">Plan</label>
                    <select 
                      value={newSub.plan} 
                      onChange={(e) => setNewSub({...newSub, plan: e.target.value})}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-xs text-white font-mono outline-none"
                    >
                      <option value="free">Free</option>
                      <option value="premium">Premium</option>
                      <option value="vip">VIP</option>
                    </select>
                  </div>
                  <div className="md:col-span-1">
                    <label className="text-xxs text-gray-500 font-mono block mb-1">Bitiş Tarihi</label>
                    <input 
                      type="text" 
                      placeholder="YYYY-MM-DD"
                      value={newSub.end_date}
                      onChange={(e) => setNewSub({...newSub, end_date: e.target.value})}
                      className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-xs text-white font-mono outline-none"
                    />
                  </div>
                  <div className="md:col-span-1 flex items-end">
                    <button 
                      type="submit"
                      className="w-full py-2 bg-teal-500 hover:bg-teal-600 text-black font-semibold rounded-lg text-xs transition-all font-mono"
                    >
                      Ekle / Kaydet
                    </button>
                  </div>
                </form>
              </div>

              {/* Members List Table */}
              <div className="glass-panel rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-[#2e303a] flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-white font-mono">BOT VE KANAL ABONELERİ</h3>
                  <span className="text-xs text-gray-500 font-mono">{filteredSubs.length} kayıt listelendi</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse font-mono text-xs">
                    <thead>
                      <tr className="border-b border-[#2e303a] text-gray-500">
                        <th className="px-6 py-3">Telegram ID</th>
                        <th className="px-6 py-3">Kullanıcı</th>
                        <th className="px-6 py-3">İsim</th>
                        <th className="px-6 py-3">Plan</th>
                        <th className="px-6 py-3">Durum</th>
                        <th className="px-6 py-3">Ödeme / Süre Bitiş</th>
                        <th className="px-6 py-3 text-right">Abonelik Süresi Güncelle</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#2e303a] text-gray-300">
                      {filteredSubs.map(sub => (
                        <tr key={sub.telegram_id} className="hover:bg-white/2 transition-colors">
                          <td className="px-6 py-3 text-teal-400">{sub.telegram_id}</td>
                          <td className="px-6 py-3 text-white">@{sub.username || 'yok'}</td>
                          <td className="px-6 py-3 font-sans">{sub.full_name || '-'}</td>
                          <td className="px-6 py-3">
                            <span className={`px-2 py-0.5 rounded text-xxs font-bold uppercase ${
                              sub.plan === 'vip' ? 'bg-amber-500/15 text-amber-400' :
                              sub.plan === 'premium' ? 'bg-teal-500/15 text-teal-400' :
                              'bg-gray-500/15 text-gray-400'
                            }`}>{sub.plan}</span>
                          </td>
                          <td className="px-6 py-3">
                            {getSubStatusBadge(sub)}
                          </td>
                          <td className="px-6 py-3 text-gray-400">
                            {sub.end_date ? sub.end_date.split(' ')[0] : 'Süresiz'}
                          </td>
                          <td className="px-6 py-3 text-right space-x-2">
                            <button 
                              onClick={() => quickExtendSubscription(sub.telegram_id, 7)}
                              className="px-2 py-1 bg-teal-500/10 hover:bg-teal-500/25 border border-teal-500/20 text-teal-400 rounded text-xxs transition-colors font-bold"
                            >
                              +7 Gün
                            </button>
                            <button 
                              onClick={() => quickExtendSubscription(sub.telegram_id, 30)}
                              className="px-2 py-1 bg-teal-500/10 hover:bg-teal-500/25 border border-teal-500/20 text-teal-400 rounded text-xxs transition-colors font-bold"
                            >
                              +30 Gün
                            </button>
                            <button 
                              onClick={() => deleteSubscriber(sub.telegram_id)}
                              className="p-1 bg-red-500/10 hover:bg-red-500/20 border border-red-500/25 text-red-400 rounded inline-flex align-middle transition-colors"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: Model & Tahmin Ayarı */}
          {activeTab === 'predictions' && (
            <div className="space-y-6">
              {/* Parameters Tuner */}
              <div className="glass-panel rounded-xl p-6">
                <div className="flex items-center gap-2 mb-4">
                  <Sliders className="w-5 h-5 text-teal-400" />
                  <h3 className="text-sm font-semibold text-white font-mono">T-PARAMETRESİ (TEMPERATURE SCALING) VE ÖZELLİK BAYRAKLARI</h3>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                  {/* Slider Control */}
                  <div className="space-y-4">
                    <div>
                      <div className="flex justify-between mb-2">
                        <label className="text-sm text-gray-300 font-medium">Model Karar Yumuşatma Sıcaklığı (T)</label>
                        <span className="text-sm text-teal-400 font-mono font-bold">{config.temperature.toFixed(2)}</span>
                      </div>
                      <input 
                        type="range" 
                        min="0.50" 
                        max="2.50" 
                        step="0.05"
                        value={config.temperature}
                        onChange={(e) => {
                          const val = parseFloat(e.target.value);
                          setConfig({ ...config, temperature: val });
                        }}
                        className="w-full accent-teal-500 bg-gray-700 h-1.5 rounded-lg appearance-none cursor-pointer"
                      />
                      <div className="flex justify-between text-xxs text-gray-500 font-mono mt-1">
                        <span>0.50 (Daha Keskin Olasılıklar)</span>
                        <span>1.15 (Varsayılan)</span>
                        <span>2.50 (Olasılıkları Dağıt/Belirsizlik Artar)</span>
                      </div>
                    </div>
                    
                    <button 
                      onClick={() => saveConfig(config)}
                      className="px-6 py-2.5 bg-teal-500 hover:bg-teal-600 text-black font-semibold rounded-lg text-sm transition-all"
                    >
                      T Ayarını Kaydet (data/admin_config.json)
                    </button>
                  </div>

                  {/* Informational Panel */}
                  <div className="bg-black/30 border border-[#2e303a] p-4 rounded-lg flex gap-3 text-xs text-gray-400">
                    <Info className="w-5 h-5 text-teal-400 shrink-0 mt-0.5" />
                    <div className="space-y-2">
                      <strong className="text-white">Matematiksel Açıklama:</strong>
                      <p>
                        Sakatlık Power Loss penaltıları uygulandıktan sonra model olasılıkları log-odds alanına gönderilir. 
                        Ardından <code>Softmax(z / T)</code> formülüyle yeniden normalize edilir. 
                        <strong> T &gt; 1.00 </strong> olasılıkları düzleştirerek daha ihtiyatlı tahminler üretir.
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Predictions Table */}
              <div className="glass-panel rounded-xl overflow-hidden">
                <div className="px-6 py-4 border-b border-[#2e303a] flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-white font-mono">BUGÜN VE YAKLAŞAN MAÇ TAHMİNLERİ</h3>
                  <span className="text-xs text-gray-500 font-mono">{predictions.length} tahmin listelendi</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="border-b border-[#2e303a] text-gray-500 font-mono text-xs">
                        <th className="px-6 py-3">Maç Tarihi</th>
                        <th className="px-6 py-3">Lig</th>
                        <th className="px-6 py-3">Karşılaşma</th>
                        <th className="px-6 py-3 text-center">H / D / A Olasılık</th>
                        <th className="px-6 py-3">Öneri Oranlar</th>
                        <th className="px-6 py-3">Güven / Tür</th>
                        <th className="px-6 py-3">Model</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#2e303a] font-mono text-xs text-gray-300">
                      {predictions.length === 0 ? (
                        <tr>
                          <td colSpan="7" className="px-6 py-8 text-center text-gray-500">
                            Aktif veya yaklaşan maç tahmini bulunmamaktadır.
                          </td>
                        </tr>
                      ) : (
                        predictions.map(pred => (
                          <tr key={pred.id} className="hover:bg-white/2 transition-colors">
                            <td className="px-6 py-3 text-gray-400">{pred.date.slice(0, 10)}</td>
                            <td className="px-6 py-3 text-teal-500">{pred.league_code}</td>
                            <td className="px-6 py-3 text-white font-semibold font-sans">
                              {pred.home_team} - {pred.away_team}
                            </td>
                            <td className="px-6 py-3 text-center">
                              <div className="flex justify-center gap-1.5 font-bold">
                                <span className={pred.predicted_result === 'H' ? 'text-teal-400' : 'text-gray-400'}>
                                  %{Math.round(pred.home_win_prob * 100)}
                                </span>
                                <span className="text-gray-600">/</span>
                                <span className={pred.predicted_result === 'D' ? 'text-teal-400' : 'text-gray-400'}>
                                  %{Math.round(pred.draw_prob * 100)}
                                </span>
                                <span className="text-gray-600">/</span>
                                <span className={pred.predicted_result === 'A' ? 'text-teal-400' : 'text-gray-400'}>
                                  %{Math.round(pred.away_win_prob * 100)}
                                </span>
                              </div>
                            </td>
                            <td className="px-6 py-3">
                              {pred.home_odds ? (
                                <div className="flex gap-2 text-xxs text-gray-500">
                                  <span>H:{pred.home_odds.toFixed(2)}</span>
                                  <span>D:{pred.draw_odds.toFixed(2)}</span>
                                  <span>A:{pred.away_odds.toFixed(2)}</span>
                                </div>
                              ) : '-'}
                            </td>
                            <td className="px-6 py-3">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-white">{pred.confidence_score}%</span>
                                <span className={`px-1.5 py-0.5 rounded text-xxs font-bold ${pred.predicted_result === 'SKIP' ? 'bg-gray-500/10 text-gray-500' : 'bg-emerald-500/10 text-emerald-400'}`}>
                                  {pred.predicted_result}
                                </span>
                              </div>
                            </td>
                            <td className="px-6 py-3 text-gray-500">{pred.model_type}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: AI Insight Sakatlık Editörü */}
          {activeTab === 'injuries' && (
            <div className="space-y-6">
              <div className="glass-panel rounded-xl p-6">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-[#2e303a] pb-6 mb-6">
                  <div>
                    <h3 className="text-sm font-semibold text-white font-mono flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-teal-400" />
                      TAKIM SAKATLIK & KADRO DEĞERİ EDİTÖRÜ (AI INSIGHT PANELDEN DÜZELTME)
                    </h3>
                    <p className="text-xs text-gray-500 mt-1 font-sans">
                      Aşağıdan takımı seçerek sakat/cezalı oyuncu verisini SQLite üzerinde elle güncelleyebilirsiniz.
                    </p>
                  </div>
                  
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-400 font-mono">Takım Seç:</span>
                    <select 
                      value={selectedTeamId}
                      onChange={handleTeamChange}
                      className="bg-[#1f2833] border border-[#2e303a] text-white text-xs font-mono rounded-lg px-4 py-2 outline-none focus:border-teal-500"
                    >
                      {teams.map(t => (
                        <option key={t.id} value={t.id}>
                          [{t.league_code}] {t.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Team Status Editor Grid */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  {/* Left Column: Stats and Info */}
                  <div className="glass-card rounded-lg p-5 space-y-4">
                    <h4 className="text-xs font-bold text-white font-mono uppercase tracking-wider">TAKIM GÜÇ BİLGİLERİ</h4>
                    
                    <div>
                      <label className="text-xxs text-gray-500 font-mono block mb-1">Takım Kadro Değeri (€ EUR)</label>
                      <input 
                        type="number" 
                        value={teamStatus.squad_value_eur}
                        onChange={(e) => setTeamStatus({...teamStatus, squad_value_eur: parseFloat(e.target.value) || 0})}
                        className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-3 py-2 text-sm text-white font-mono outline-none"
                      />
                    </div>

                    <div>
                      <div className="flex justify-between mb-1">
                        <label className="text-xxs text-gray-500 font-mono">Sakatlık Kaynaklı Tahmini Güç Kaybı (%)</label>
                        <span className="text-xs text-teal-400 font-mono font-bold">{teamStatus.power_loss_pct}%</span>
                      </div>
                      <input 
                        type="range" 
                        min="0" 
                        max="50" 
                        step="0.5"
                        value={teamStatus.power_loss_pct}
                        onChange={(e) => setTeamStatus({...teamStatus, power_loss_pct: parseFloat(e.target.value)})}
                        className="w-full accent-teal-500 bg-gray-700 h-1.5 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>

                    <div className="pt-4 border-t border-[#2e303a]">
                      <button 
                        onClick={saveTeamInjuryStatus}
                        className="w-full py-2.5 bg-teal-500 hover:bg-teal-600 text-black font-bold rounded-lg text-sm transition-all"
                      >
                        Değişiklikleri SQL'e Kaydet
                      </button>
                    </div>
                  </div>

                  {/* Middle Column: Injured Players */}
                  <div className="glass-card rounded-lg p-5 space-y-4">
                    <div className="flex justify-between items-center border-b border-[#2e303a] pb-2">
                      <h4 className="text-xs font-bold text-white font-mono uppercase tracking-wider">SAKAT OYUNCULAR ({teamStatus.injured_players.length})</h4>
                    </div>

                    {/* Add Injury form */}
                    <div className="bg-black/20 p-3 rounded-lg space-y-2 border border-[#2e303a]">
                      <input 
                        type="text" 
                        placeholder="Oyuncu İsmi"
                        value={newInjury.name}
                        onChange={(e) => setNewInjury({...newInjury, name: e.target.value})}
                        className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-2 py-1 text-xs text-white outline-none"
                      />
                      <div className="grid grid-cols-2 gap-2">
                        <input 
                          type="text" 
                          placeholder="Sakatlık Türü"
                          value={newInjury.injury}
                          onChange={(e) => setNewInjury({...newInjury, injury: e.target.value})}
                          className="bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-2 py-1 text-xs text-white outline-none"
                        />
                        <select
                          value={newInjury.severity}
                          onChange={(e) => setNewInjury({...newInjury, severity: e.target.value})}
                          className="bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-2 py-1 text-xs text-white font-mono outline-none"
                        >
                          <option value="minor">Hafif (Minor)</option>
                          <option value="medium">Orta (Medium)</option>
                          <option value="major">Ağır (Major)</option>
                        </select>
                      </div>
                      <button 
                        type="button"
                        onClick={addInjury}
                        className="w-full py-1 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 text-teal-400 font-semibold rounded text-xxs transition-colors"
                      >
                        Listeye Ekle
                      </button>
                    </div>

                    {/* Injury List */}
                    <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                      {teamStatus.injured_players.map((inj, index) => (
                        <div key={index} className="flex justify-between items-center bg-black/40 p-2.5 rounded border border-[#2e303a]">
                          <div className="text-xs">
                            <p className="font-semibold text-white">{inj.name}</p>
                            <p className="text-xxs text-gray-500">{inj.injury} - <span className="uppercase text-amber-500">{inj.severity}</span></p>
                          </div>
                          <div className="flex gap-2">
                            <button 
                              onClick={() => addKeyAbsence(inj.name)}
                              className="px-1.5 py-0.5 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/20 rounded text-xxs font-bold"
                            >
                              Kritik Yap
                            </button>
                            <button 
                              onClick={() => removeInjury(index)}
                              className="text-red-400 hover:text-red-300"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Right Column: Suspended and Key Absences */}
                  <div className="glass-card rounded-lg p-5 space-y-4">
                    {/* Suspensions Section */}
                    <div>
                      <h4 className="text-xs font-bold text-white font-mono uppercase tracking-wider mb-2 border-b border-[#2e303a] pb-2">CEZALI OYUNCULAR ({teamStatus.suspended_players.length})</h4>
                      
                      {/* Add suspension form */}
                      <div className="bg-black/20 p-3 rounded-lg space-y-2 border border-[#2e303a] mb-3">
                        <input 
                          type="text" 
                          placeholder="Cezalı Oyuncu İsmi"
                          value={newSuspension.name}
                          onChange={(e) => setNewSuspension({...newSuspension, name: e.target.value})}
                          className="w-full bg-black/40 border border-[#2e303a] focus:border-teal-500 rounded-lg px-2 py-1 text-xs text-white outline-none"
                        />
                        <button 
                          type="button"
                          onClick={addSuspension}
                          className="w-full py-1 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 text-teal-400 font-semibold rounded text-xxs transition-colors"
                        >
                          Cezalı Ekle
                        </button>
                      </div>

                      {/* Suspensions list */}
                      <div className="space-y-2 max-h-36 overflow-y-auto">
                        {teamStatus.suspended_players.map((sus, index) => (
                          <div key={index} className="flex justify-between items-center bg-black/40 p-2 rounded border border-[#2e303a]">
                            <span className="text-xs text-white">{sus.name}</span>
                            <button 
                              onClick={() => removeSuspension(index)}
                              className="text-red-400 hover:text-red-300"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Key Absences Section */}
                    <div className="pt-2 border-t border-[#2e303a]">
                      <h4 className="text-xs font-bold text-white font-mono uppercase tracking-wider mb-2">KRİTİK EKSİKLER (POWER LOSS ETKİSİ YÜKSEK)</h4>
                      <div className="flex flex-wrap gap-1.5">
                        {teamStatus.key_absences.map((name, index) => (
                          <span key={index} className="px-2 py-1 bg-red-500/15 hover:bg-red-500/25 border border-red-500/20 text-red-400 rounded-full text-xxs flex items-center gap-1 font-mono">
                            {name}
                            <button 
                              onClick={() => removeKeyAbsence(index)}
                              className="font-bold hover:text-white"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: Lig ve Tahmin Algoritmaları Konfigürasyonu */}
          {activeTab === 'leagues' && (
            <div className="space-y-6">
              {/* Leagues Config */}
              <div className="glass-panel rounded-xl p-6">
                <h3 className="text-sm font-semibold text-white font-mono mb-4 flex items-center gap-2">
                  <Map className="w-4 h-4 text-teal-400" />
                  SİSTEME ENTEGRE LİGLER VE TAHMİN ALGORİTMALARI
                </h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* League list */}
                  <div className="space-y-4">
                    <div className="bg-black/30 border border-[#2e303a] p-4 rounded-lg">
                      <div className="flex justify-between items-center mb-2">
                        <span className="text-xs font-bold text-white font-mono">Avrupa Major Ligleri & Süper Lig</span>
                        <span className="px-2 py-0.5 bg-teal-500/10 text-teal-400 rounded text-xxs font-mono font-bold">ML Stacking</span>
                      </div>
                      <p className="text-xxs text-gray-500 mb-3 font-mono">
                        Ligler: Premier League (E0), La Liga (SP1), Serie A (I1), Bundesliga (D1), Süper Lig (T1)
                      </p>
                      <p className="text-xs text-gray-400">
                        Kadro değerleri, form grafiği ve 32 yapısal öznitelik toplanır. <strong>XGBoost + LightGBM + Poisson model birleşimi</strong> ile Platt Scaling kalibrasyonu yapılarak 1X2 olasılıkları hesaplanır.
                      </p>
                    </div>

                    <div className="bg-black/30 border border-[#2e303a] p-4 rounded-lg">
                      <div className="flex justify-between items-center mb-2">
                        <span className="text-xs font-bold text-white font-mono">Yaz Ligleri (Summer Leagues)</span>
                        <span className="px-2 py-0.5 bg-amber-500/10 text-amber-400 rounded text-xxs font-mono font-bold">Hybrid Modifiers</span>
                      </div>
                      <p className="text-xxs text-gray-500 mb-3 font-mono">
                        Ligler: Norveç Eliteserien (NOR), Brezilya Serie A (BRA)
                      </p>
                      <p className="text-xs text-gray-400">
                        Avrupa modellerinin dengesini bozmamak adına, base modellerin üzerine <strong>yapay çim avantajı</strong> (Norveç için +%15 home boost) ve <strong>coğrafi seyahat/Libertadores yorgunluğu</strong> (Brezilya için -%15 deplasman penaltısı) modifikasyonları uygulanır.
                      </p>
                    </div>

                    <div className="bg-black/30 border border-[#2e303a] p-4 rounded-lg">
                      <div className="flex justify-between items-center mb-2">
                        <span className="text-xs font-bold text-white font-mono">Dünya Kupası 2026</span>
                        <span className="px-2 py-0.5 bg-sky-500/10 text-sky-400 rounded text-xxs font-mono font-bold">Three-Tier Inference</span>
                      </div>
                      <p className="text-xxs text-gray-500 mb-3 font-mono">
                        Turnuva Kodu: WC26
                      </p>
                      <p className="text-xs text-gray-400">
                        Üç aşamalı tahmin zinciri: 18:30\'da muhtemel kadrolarla bülten, 23:00\'de piyasa hareketleri ile kupon, maçtan 45 dk önce resmi ilk 11 kalitesiyle **Monte Carlo simülasyonu** ve Sharp Money Delta analizi.
                      </p>
                    </div>
                  </div>

                  {/* Future Roadmap & Adding Leagues */}
                  <div className="glass-card rounded-lg p-5 flex flex-col justify-between border border-[#2e303a]/80">
                    <div>
                      <h4 className="text-xs font-bold text-white font-mono uppercase tracking-wider mb-2">YOL HARİTASI & GELECEKTE EKLENECEK FONSİYONLAR</h4>
                      <p className="text-xs text-gray-400 mb-4">
                        Aşağıdaki özellikler sisteme eklendiğinde admin paneli üzerinden anında konfigüre edilip görüntülenecektir:
                      </p>
                      
                      <ul className="space-y-2.5 font-mono text-xxs text-gray-500">
                        <li className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-teal-500"></span>
                          <span className="text-gray-300">USA MLS:</span> Designated Player (DP) maaş ve önem algoritması.
                        </li>
                        <li className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-teal-500"></span>
                          <span className="text-gray-300">İsveç Allsvenskan:</span> Plastik zemin ve soğuk iklim faktörü.
                        </li>
                        <li className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-teal-500"></span>
                          <span className="text-gray-300">Japonya J1:</span> Tayfun fırtınaları ve yüksek nem çekilme parametreleri.
                        </li>
                        <li className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                          <span className="text-gray-300">Canlı Maç Tahmin Modülü:</span> Canlı bahis oran hareketlerinin anlık taranması.
                        </li>
                      </ul>
                    </div>

                    <div className="mt-6 pt-4 border-t border-[#2e303a] space-y-3">
                      <div className="flex gap-2">
                        <input 
                          type="text" 
                          disabled
                          placeholder="Yeni Lig Kodu (Örn: MLS)"
                          className="bg-black/25 border border-[#2e303a] rounded px-3 py-1.5 text-xxs outline-none cursor-not-allowed w-full"
                        />
                        <button 
                          disabled
                          className="px-3 py-1.5 bg-[#1f2833] border border-[#2e303a] text-gray-500 text-xxs font-bold rounded cursor-not-allowed inline-flex items-center gap-1"
                        >
                          <Plus className="w-3.5 h-3.5" /> Lig Ekle
                        </button>
                      </div>
                      <span className="text-xxxs text-gray-600 block italic text-center">*Lig ekleme ve canlı veri motoru geliştirme aşamasındadır.</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;

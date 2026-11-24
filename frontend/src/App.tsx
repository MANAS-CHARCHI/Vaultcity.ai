import { useState, useEffect, useRef, useCallback } from 'react';
import { Navigation } from './components/Navigation';
import { VaultView } from './components/VaultView';
import { UploadView } from './components/UploadView';
import { ChatView } from './components/ChatView';
import { ActivityView } from './components/ActivityView';
import { SettingsView } from './components/SettingsView';
import { PwaInstallButton } from './components/PwaInstallButton';
import { AuthView } from './components/AuthView';
import { ShareModal } from './components/ShareModal';
import { TabType, DocumentFile, ChatMessage, ActivityItem, StagedFile, UserProfile, StagedPage, SecurityAlert } from './types';
import {
  checkHealth,
  verifyAuthSession,
  apiRefreshToken,
  apiLogout,
  getStoredUser,
  apiUpdateProfile,
  apiListFiles,
  apiUploadFile,
  apiRenameFile,
  apiDeleteFile,
  apiDownloadFile,
  apiGetActivityLogs,
  apiDismissSecurityAlert,
  apiSendChatMessage,
} from './services/api';
import { X, FileText, Download, ChevronLeft, ChevronRight, Layers, Share2, ShieldAlert } from 'lucide-react';

export function App() {
  // Requirement: AI Assistant chatbot is the default active view on start
  // currentTab controls immediate navbar indicator feedback
  const [currentTab, setCurrentTab] = useState<TabType>('chat');
  // displayedTab is the view currently rendered in the DOM (swaps after shutdown)
  const [displayedTab, setDisplayedTab] = useState<TabType>('chat');
  // Smooth Section Transition: 'idle' | 'shutting-down' | 'running-in'
  const [transitionStage, setTransitionStage] = useState<'idle' | 'shutting-down' | 'running-in'>('idle');
  const targetTabRef = useRef<TabType>('chat');

  const [previewDoc, setPreviewDoc] = useState<DocumentFile | null>(null);
  const [sharingDoc, setSharingDoc] = useState<DocumentFile | null>(null);
  const [activePageIdx, setActivePageIdx] = useState<number>(0);

  // Day / Night Theme State (defaults to clean Day Mode)
  const [isDark, setIsDark] = useState<boolean>(() => {
    const saved = localStorage.getItem('vault_theme');
    if (saved) return saved === 'dark';
    return false;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (isDark) {
      root.classList.add('dark');
      localStorage.setItem('vault_theme', 'dark');
    } else {
      root.classList.remove('dark');
      localStorage.setItem('vault_theme', 'light');
    }
  }, [isDark]);

  const toggleTheme = () => setIsDark((prev) => !prev);

  // User Authentication State (Enforces Strict HttpOnly Cookie & Periodic Authorization)
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(() => {
    return getStoredUser();
  });

  const handleLoginSuccess = (user: UserProfile) => {
    setCurrentUser(user);
  };

  const handleLogout = async () => {
    setCurrentUser(null);
    await apiLogout();
  };

  // 1. Cold-Start Verification on App Mount
  useEffect(() => {
    verifyAuthSession().then((user) => {
      if (!user) {
        if (!getStoredUser()) {
          handleLogout();
        }
      } else {
        setCurrentUser(user);
      }
    });
  }, []);

  // 2. Proactive Early Token Refresh & Tab Focus / Wake-from-Sleep Guard
  // Access tokens expire in 5 minutes (300s). We proactively refresh early at 3.5 minutes (210s)
  // so the access token NEVER expires while user is active, completely eliminating 401 logouts.
  // If the 7-day refresh token is valid, the user is NEVER logged out.
  useEffect(() => {
    if (!currentUser) return;

    // Proactively refresh access token every 3.5 minutes (210,000 ms) before 5-minute expiry
    const refreshInterval = setInterval(async () => {
      await apiRefreshToken();
    }, 210000);

    // When tab regains focus or becomes visible (e.g. laptop wakes up from sleep),
    // proactively ensure access token is fresh before any component queries backend.
    const handleVisibilityOrFocus = async () => {
      if (document.visibilityState === 'visible') {
        const refreshed = await apiRefreshToken();
        if (refreshed) {
          const user = await verifyAuthSession();
          if (user) setCurrentUser(user);
        }
      }
    };

    // Cross-tab synchronization: if logged out or user purged in another tab
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'vault_user' && !e.newValue) {
        handleLogout();
      }
    };

    // Custom unauthorized event dispatched ONLY when refresh token itself is dead (7-day expiry)
    const handleUnauthorized = () => {
      handleLogout();
    };

    window.addEventListener('focus', handleVisibilityOrFocus);
    document.addEventListener('visibilitychange', handleVisibilityOrFocus);
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('vault:unauthorized', handleUnauthorized);

    return () => {
      clearInterval(refreshInterval);
      window.removeEventListener('focus', handleVisibilityOrFocus);
      document.removeEventListener('visibilitychange', handleVisibilityOrFocus);
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('vault:unauthorized', handleUnauthorized);
    };
  }, [currentUser]);

  const handleUpdateFullName = async (newName: string) => {
    if (!currentUser) return;
    try {
      const updatedUser = await apiUpdateProfile(newName);
      setCurrentUser(updatedUser);
    } catch (err) {
      console.warn('Backend profile update failed, falling back to local cache:', err);
      const updatedUser: UserProfile = {
        ...currentUser,
        name: newName,
        fullName: newName,
      };
      setCurrentUser(updatedUser);
      localStorage.setItem('vault_user', JSON.stringify(updatedUser));
    }

    setActivities((prev) => [
      {
        id: `act-${Date.now()}`,
        action: 'Profile Updated',
        target: `Full name updated to "${newName}"`,
        timestamp: new Date().toISOString(),
        status: 'info',
      },
      ...prev,
    ]);
  };

  const [systemStatus, setSystemStatus] = useState({
    postgres: 'connected',
    pgvector: 'connected',
  });

  const [documents, setDocuments] = useState<DocumentFile[]>([]);
// Strict filter to ensure infected/malware file traces are NEVER stored in localStorage or client cache
const isInfectedFileItem = (item: any): boolean => {
  if (!item) return false;
  const action = String(item.action || "").toLowerCase();
  const target = String(item.target || "").toLowerCase();
  const status = String(item.status || "").toLowerCase();
  const name = String(item.name || item.filename || "").toLowerCase();

  return (
    action.includes("infected") ||
    action.includes("malware") ||
    action.includes("virus") ||
    target.includes("eicar") ||
    target.includes("infected") ||
    target.includes("malware") ||
    name.includes("eicar") ||
    status === "infected" ||
    status === "blocked"
  );
};

// Scrub any infected file records or malicious tokens from browser localStorage completely
const scrubInfectedLocalStorage = (): void => {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key) continue;

      if (key.startsWith("vault_activities_cache_")) {
        const raw = localStorage.getItem(key);
        if (raw) {
          try {
            const list = JSON.parse(raw);
            if (Array.isArray(list)) {
              const clean = list.filter((item) => !isInfectedFileItem(item));
              if (clean.length !== list.length) {
                localStorage.setItem(key, JSON.stringify(clean));
              }
            }
          } catch (_) {}
        }
      } else {
        const lowerKey = key.toLowerCase();
        if (
          lowerKey.includes("infected") ||
          lowerKey.includes("eicar") ||
          lowerKey.includes("virus") ||
          lowerKey.includes("malware")
        ) {
          localStorage.removeItem(key);
        }
      }
    }
  } catch (_) {}
};

  // User Device Cache (Browser LocalStorage) for instant 0ms activity loading (strictly excludes any infected file data)
  const [activities, setActivities] = useState<ActivityItem[]>(() => {
    scrubInfectedLocalStorage();
    const user = getStoredUser();
    if (user?.id) {
      try {
        const cached = localStorage.getItem('vault_activities_cache_' + user.id);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (Array.isArray(parsed) && parsed.length > 0) {
            return parsed.filter((item: ActivityItem) => !isInfectedFileItem(item));
          }
        }
      } catch (e) {}
    }
    return [];
  });
  const [activitiesPagination, setActivitiesPagination] = useState<{
    page: number;
    hasMore: boolean;
    totalCount: number;
    isLoadingMore: boolean;
  }>({
    page: 1,
    hasMore: false,
    totalCount: 0,
    isLoadingMore: false,
  });
  const [storageUsage, setStorageUsage] = useState<{ usedBytes: number; quotaBytes: number }>({
    usedBytes: 0,
    quotaBytes: 100 * 1024 * 1024,
  });

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'msg-1',
      sender: 'assistant',
      text: 'Vault City ready. Upload your personal documents in the Upload tab to scan, encrypt, and index them for private semantic search.',
      timestamp: new Date().toISOString(),
    },
  ]);

  const [securityAlerts, setSecurityAlerts] = useState<SecurityAlert[]>([]);
  const dismissedAlertIdsRef = useRef<Set<string>>(new Set());

  const handleDismissAlert = useCallback((alertId: string) => {
    dismissedAlertIdsRef.current.add(alertId);
    setSecurityAlerts((prev) => prev.filter((a) => a.id !== alertId));
    apiDismissSecurityAlert(alertId);
  }, []);

  // Notification is automatically removed after 5 seconds or when user manually dismisses it
  useEffect(() => {
    if (securityAlerts.length === 0) return;

    const timers = securityAlerts.map((alert) =>
      setTimeout(() => {
        handleDismissAlert(alert.id);
      }, 5000)
    );

    return () => {
      timers.forEach((t) => clearTimeout(t));
    };
  }, [securityAlerts, handleDismissAlert]);

  // Fetch real files, alerts & audit logs from PostgreSQL/Redis/S3 whenever user signs in
  const refreshVaultData = async () => {
    try {
      const [vaultData, logsData] = await Promise.all([
        apiListFiles(),
        apiGetActivityLogs(1, 10),
      ]);
      setDocuments(vaultData.files);
      setStorageUsage({
        usedBytes: vaultData.usedBytes,
        quotaBytes: vaultData.quotaBytes,
      });
      setActivities(logsData.activities);
      setActivitiesPagination({
        page: 1,
        hasMore: logsData.hasMore,
        totalCount: logsData.totalCount,
        isLoadingMore: false,
      });

      // Cache the last 10 activities on user's device (browser cache) - NEVER store infected file data
      if (currentUser?.id && logsData.activities.length > 0) {
        try {
          const safeActivities = logsData.activities
            .filter((item) => !isInfectedFileItem(item))
            .slice(0, 10);
          localStorage.setItem(
            'vault_activities_cache_' + currentUser.id,
            JSON.stringify(safeActivities)
          );
        } catch (e) {}
      }

      if (vaultData.securityAlerts) {
        const incomingAlerts = vaultData.securityAlerts.filter(
          (a) => !dismissedAlertIdsRef.current.has(a.id)
        );
        // Only update state if IDs changed so we don't reset the 5-second auto-dismiss timer
        setSecurityAlerts((prev) => {
          const prevIds = prev.map((a) => a.id).sort().join(',');
          const incomingIds = incomingAlerts.map((a) => a.id).sort().join(',');
          if (prevIds === incomingIds) {
            return prev;
          }
          return incomingAlerts;
        });
      }
    } catch (err) {
      console.warn('Failed to load vault data:', err);
    }
  };

  const handleLoadMoreActivities = async () => {
    if (activitiesPagination.isLoadingMore || !activitiesPagination.hasMore) return;
    setActivitiesPagination((prev) => ({ ...prev, isLoadingMore: true }));
    try {
      const nextPage = activitiesPagination.page + 1;
      const data = await apiGetActivityLogs(nextPage, 10);
      setActivities((prev) => [...prev, ...data.activities]);
      setActivitiesPagination({
        page: nextPage,
        hasMore: data.hasMore,
        totalCount: data.totalCount,
        isLoadingMore: false,
      });
    } catch (err) {
      console.warn('Failed to load older activities from DB:', err);
      setActivitiesPagination((prev) => ({ ...prev, isLoadingMore: false }));
    }
  };

  useEffect(() => {
    scrubInfectedLocalStorage();
    if (currentUser) {
      // Immediate load from browser device cache on user switch (strictly excluding infected files)
      try {
        const cached = localStorage.getItem('vault_activities_cache_' + currentUser.id);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (Array.isArray(parsed) && parsed.length > 0) {
            const clean = parsed.filter((item: ActivityItem) => !isInfectedFileItem(item));
            setActivities(clean);
          }
        }
      } catch (e) {}
      refreshVaultData();
    } else {
      setDocuments([]);
      setActivities([]);
      setSecurityAlerts([]);
      dismissedAlertIdsRef.current.clear();
      setStorageUsage({ usedBytes: 0, quotaBytes: 100 * 1024 * 1024 });
      setActivitiesPagination({ page: 1, hasMore: false, totalCount: 0, isLoadingMore: false });
    }
  }, [currentUser?.id]);

  // Poll automatically every 2s while any file is scanning or classifying
  const hasPendingScan = documents.some(
    (doc) => doc.status === 'scanning' || doc.status === 'classifying'
  );
  useEffect(() => {
    if (!currentUser || !hasPendingScan) return;
    const timer = setInterval(() => {
      refreshVaultData();
    }, 2000);
    return () => clearInterval(timer);
  }, [currentUser?.id, hasPendingScan]);

  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    async function fetchStatus() {
      const data = await checkHealth();
      if (data && data.checks) {
        setSystemStatus({
          postgres: data.checks.postgres || 'connected',
          pgvector: data.checks.pgvector || 'connected',
        });
      }
    }
    fetchStatus();
  }, []);

  // Orchestrated section transition: shuts down active view, then runs target view
  const handleTabChange = (nextTab: TabType) => {
    // Dismiss any active document preview modal
    setPreviewDoc(null);

    // If already on target tab and idle, no-op
    if (nextTab === targetTabRef.current && transitionStage === 'idle') {
      return;
    }

    targetTabRef.current = nextTab;
    setCurrentTab(nextTab); // Immediate feedback on top/bottom navbar

    // Trigger graceful shutdown of current section
    setTransitionStage('shutting-down');
  };

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    if (transitionStage === 'shutting-down') {
      // 140ms graceful shutdown (fade & glide down)
      timer = setTimeout(() => {
        setDisplayedTab(targetTabRef.current);
        window.scrollTo({ top: 0, behavior: 'instant' });
        setTransitionStage('running-in');
      }, 140);
    } else if (transitionStage === 'running-in') {
      // 220ms smooth run-in (fade & rise to place)
      timer = setTimeout(() => {
        setTransitionStage('idle');
        if (targetTabRef.current !== displayedTab) {
          setDisplayedTab(targetTabRef.current);
        }
      }, 220);
    }
    return () => clearTimeout(timer);
  }, [transitionStage, displayedTab]);

  // Real API upload handler: Quarantine S3 -> ClamAV & ML Scan -> Promote to Secured S3 & pgvector
  const handleConfirmUpload = async (
    files: {
      file: File;
      finalName: string;
      category: StagedFile['category'];
      pages?: StagedPage[];
    }[]
  ) => {
    const uploadedDocs: DocumentFile[] = [];
    const errors: string[] = [];

    for (const item of files) {
      // If user bundled multiple image pages, upload each page cleanly or primary file
      const filesToSend =
        item.pages && item.pages.length > 1
          ? item.pages.map((p, idx) => ({
              file: p.file,
              customName: `${item.finalName.replace(/\.[^/.]+$/, '')}_p${idx + 1}`,
              category: item.category,
            }))
          : [{ file: item.file, customName: item.finalName, category: item.category }];

      for (const target of filesToSend) {
        try {
          const uploaded = await apiUploadFile({
            file: target.file,
            customName: target.customName,
            category: target.category,
          });
          uploadedDocs.push(uploaded);
        } catch (err: any) {
          errors.push(`${target.customName}: ${err?.message || 'Upload failed'}`);
        }
      }
    }

    if (uploadedDocs.length > 0) {
      setDocuments((prev) => [...uploadedDocs, ...prev]);
      await refreshVaultData();
      handleTabChange('vault');
    }

    if (errors.length > 0) {
      throw new Error(errors.join(' | '));
    }
  };

  // Real API Document Rename Handler
  const handleRenameDocument = async (id: string, newName: string) => {
    try {
      const updated = await apiRenameFile(id, newName);
      setDocuments((prev) => prev.map((doc) => (doc.id === id ? updated : doc)));
      await refreshVaultData();
    } catch (err) {
      console.error('Rename failed:', err);
    }
  };

  // Real API Document Delete Handler (removes S3 object, pgvector chunks, and DB row)
  const handleDeleteDocument = async (id: string) => {
    try {
      await apiDeleteFile(id);
      setDocuments((prev) => prev.filter((doc) => doc.id !== id));
      if (previewDoc?.id === id) setPreviewDoc(null);
      await refreshVaultData();
    } catch (err) {
      console.error('Delete failed:', err);
    }
  };

  // Real API Document Download Handler (Secured S3)
  const handleDownloadDocument = async (doc: DocumentFile) => {
    try {
      await apiDownloadFile(doc.id, doc.name);
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  // Clickable Document Reference in Chat -> Opens In-Browser Preview Modal immediately
  const handleOpenDocumentFromChat = (filename: string) => {
    const clean = filename.replace(/^@va\//, '').trim().toLowerCase();
    const matched = documents.find((doc) => {
      const dName = doc.name.toLowerCase();
      const oName = (doc.originalName || '').toLowerCase();
      return (
        dName === clean ||
        oName === clean ||
        dName.includes(clean) ||
        clean.includes(dName)
      );
    });
    if (matched) {
      setPreviewDoc(matched);
      setActivePageIdx(0);
    }
  };

  const handleSendMessage = async (query: string) => {
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text: query,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const res = await apiSendChatMessage(query);
      const assistantMsg: ChatMessage = {
        id: res.id || `asst-${Date.now()}`,
        sender: 'assistant',
        text: res.content,
        timestamp: res.created_at || new Date().toISOString(),
        sources: res.sources,
        interactiveOptions: res.interactive_options,
        strategyUsed: res.strategy_used,
        tokensUsed: res.tokens_used,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      // Fallback to local search if offline or network error
      const indexedDocs = documents.filter((d) => d.status === 'indexed');
      let reply = '';
      let sources: string[] = [];

      if (indexedDocs.length === 0) {
        reply =
          'Your vault has no indexed documents yet. Go to the Upload tab to upload, virus-scan, and index your files into pgvector first.';
      } else {
        const q = query.toLowerCase();
        const matches = indexedDocs.filter(
          (d) =>
            d.name.toLowerCase().includes(q) ||
            (d.description && d.description.toLowerCase().includes(q)) ||
            (d.category && d.category.toLowerCase().includes(q))
        );
        const topDocs = matches.length > 0 ? matches.slice(0, 3) : indexedDocs.slice(0, 3);
        sources = topDocs.map((d) => d.name);
        reply =
          `Searched ${indexedDocs.length} verified document(s) in your vault:\n` +
          topDocs
            .map((d) => `• ${d.name} (${d.category || 'Other'}): ${d.description || 'Indexed in pgvector.'}`)
            .join('\n');
      }

      const assistantMsg: ChatMessage = {
        id: `asst-${Date.now()}`,
        sender: 'assistant',
        text: reply,
        timestamp: new Date().toISOString(),
        sources,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  // If not logged in, render the Auth View (Login, Sign In, Password Reset)
  if (!currentUser) {
    return (
      <>
        <AuthView
          onLoginSuccess={handleLoginSuccess}
          isDark={isDark}
          onToggleTheme={toggleTheme}
        />
        <PwaInstallButton variant="floating" />
      </>
    );
  }

  return (
    <div className="min-h-screen pt-14 pb-14 md:pb-0 bg-[#FFFCF9] dark:bg-[#140E0A] bg-grid-pattern text-stone-900 dark:text-[#FAF5F0] flex flex-col font-sans transition-colors duration-200">
      {/* Top Navbar & Mobile Bottom Tab Switcher */}
      <Navigation
        currentTab={currentTab}
        onSelectTab={handleTabChange}
        systemStatus={systemStatus}
        isDark={isDark}
        onToggleTheme={toggleTheme}
        currentUser={currentUser}
        onLogout={handleLogout}
      />

      {/* Main Content Area */}
      <main
        className={`flex-1 w-full max-w-5xl mx-auto transition-all ${
          displayedTab === 'chat'
            ? 'p-0 md:px-4 md:py-5 md:py-6 md:pb-8 h-[calc(100vh-7rem)] h-[calc(100dvh-7rem)] md:h-auto overflow-hidden md:overflow-visible flex flex-col'
            : 'px-3 sm:px-4 py-4 sm:py-6 pb-20 md:pb-8 min-h-0'
        }`}
      >
        {/* Security Alerts Banner for Infected Files */}
        {securityAlerts.length > 0 && (
          <div className="mb-4 px-2 sm:px-0 space-y-2.5">
            {securityAlerts.map((alert) => (
              <div
                key={alert.id}
                role="alert"
                className="relative flex items-start sm:items-center justify-between gap-3 p-3.5 rounded-xl border border-rose-500/35 bg-rose-500/10 dark:bg-rose-950/40 backdrop-blur-sm text-rose-900 dark:text-rose-100 shadow-sm transition-all"
              >
                <div className="flex items-start sm:items-center gap-3 min-w-0">
                  <div className="p-2 rounded-lg bg-rose-500/20 text-rose-600 dark:text-rose-400 shrink-0 mt-0.5 sm:mt-0">
                    <ShieldAlert className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-xs sm:text-sm text-rose-900 dark:text-rose-100">
                        Security Alert: Infected File Removed
                      </span>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-rose-500/20 text-rose-700 dark:text-rose-300 border border-rose-500/30">
                        {alert.threatName}
                      </span>
                    </div>
                    <p className="text-xs text-rose-700 dark:text-rose-300 mt-0.5 break-words">
                      <span className="font-mono font-medium">{alert.filename}</span> was detected as infected and has been automatically removed from your vault for security.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleDismissAlert(alert.id)}
                  aria-label="Dismiss security alert"
                  className="p-1 rounded-md text-rose-600 dark:text-rose-400 hover:text-rose-900 dark:hover:text-white hover:bg-rose-500/20 transition-colors shrink-0"
                  title="Dismiss notification"
                >
                  <X className="w-4 h-4" />
                </button>

                {/* 5-second auto-remove progress indicator */}
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-rose-500/15 overflow-hidden rounded-b-xl">
                  <div className="h-full bg-rose-500/70 animate-alert-progress" />
                </div>
              </div>
            ))}
          </div>
        )}

        <div
          className={`w-full ${
            displayedTab === 'chat' ? 'h-full flex-1 flex flex-col min-h-0 overflow-hidden' : ''
          } ${
            transitionStage === 'shutting-down'
              ? 'animate-section-shutdown'
              : transitionStage === 'running-in'
              ? 'animate-section-run'
              : ''
          }`}
        >
          {displayedTab === 'vault' && (
            <VaultView
              documents={documents}
              onDeleteDocument={handleDeleteDocument}
              onPreviewDocument={(doc) => {
                setPreviewDoc(doc);
                setActivePageIdx(0);
              }}
              onRenameDocument={handleRenameDocument}
              onShareDocument={(doc) => setSharingDoc(doc)}
              onDownloadDocument={handleDownloadDocument}
              onUploadClick={() => handleTabChange('upload')}
            />
          )}

          {displayedTab === 'upload' && (
            <UploadView onConfirmUpload={handleConfirmUpload} />
          )}

          {displayedTab === 'chat' && (
            <ChatView
              messages={messages}
              onSendMessage={handleSendMessage}
              isLoading={isLoading}
              onOpenDocument={handleOpenDocumentFromChat}
            />
          )}

          {displayedTab === 'activity' && (
            <ActivityView
              activities={activities}
              hasMore={activitiesPagination.hasMore}
              totalCount={activitiesPagination.totalCount}
              isLoadingMore={activitiesPagination.isLoadingMore}
              onLoadMore={handleLoadMoreActivities}
            />
          )}

          {displayedTab === 'settings' && (
            <SettingsView
              currentUser={currentUser}
              onUpdateFullName={handleUpdateFullName}
              onLogout={handleLogout}
              usedBytes={storageUsage.usedBytes}
              quotaBytes={storageUsage.quotaBytes}
            />
          )}
        </div>
      </main>

      {/* In-Browser Document Preview Modal */}
      {previewDoc && (
        <div className="fixed inset-0 z-50 bg-black/60 dark:bg-black/85 backdrop-blur-md flex items-center justify-center p-3 sm:p-4">
          <div className="bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#382A20] rounded-2xl max-w-lg w-full p-4 space-y-3.5 shadow-2xl font-mono max-h-[85vh] max-h-[85dvh] overflow-y-auto">
            <div className="flex items-center justify-between pb-2 border-b border-stone-100 dark:border-stone-800">
              <div className="flex items-center space-x-2 min-w-0">
                <FileText className="w-4 h-4 text-stone-700 dark:text-stone-300 flex-shrink-0" />
                <div className="min-w-0">
                  <h3 className="text-xs font-bold text-stone-900 dark:text-white truncate max-w-[260px] sm:max-w-sm">
                    {previewDoc.name}
                  </h3>
                  <div className="flex items-center space-x-1.5 text-[10px]">
                    <span className="text-stone-400">@vault.city/{previewDoc.category?.toLowerCase() || 'docs'}</span>
                    {previewDoc.isPii && (
                      <span className="px-1.5 py-0.2 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/25 font-bold text-[9px]">
                        PII
                      </span>
                    )}
                    <span className="px-1.5 py-0.2 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20 text-[9px]">
                      Indexed
                    </span>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setPreviewDoc(null)}
                className="p-1 rounded-md text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Multi-Page Navigation Bar */}
            {previewDoc.pages && previewDoc.pages.length > 1 && (
              <div className="flex items-center justify-between bg-stone-100 dark:bg-stone-900/80 px-3 py-1.5 rounded-lg border border-stone-200 dark:border-stone-800 text-xs">
                <button
                  type="button"
                  disabled={activePageIdx === 0}
                  onClick={() => setActivePageIdx((p) => Math.max(0, p - 1))}
                  className={`flex items-center space-x-1 px-2 py-0.5 rounded transition-colors ${
                    activePageIdx === 0
                      ? 'text-stone-300 dark:text-stone-700 cursor-not-allowed'
                      : 'text-stone-700 dark:text-stone-200 hover:bg-stone-200 dark:hover:bg-stone-800'
                  }`}
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  <span className="text-[10px]">Prev</span>
                </button>

                <div className="flex items-center space-x-1.5 font-mono text-[10px] text-stone-700 dark:text-stone-300">
                  <Layers className="w-3 h-3 text-emerald-500" />
                  <span className="font-semibold">
                    Page {activePageIdx + 1} of {previewDoc.pages.length}
                  </span>
                  <span className="text-stone-400">
                    ({previewDoc.pages[activePageIdx]?.name || `Page ${activePageIdx + 1}`})
                  </span>
                </div>

                <button
                  type="button"
                  disabled={activePageIdx >= previewDoc.pages.length - 1}
                  onClick={() => setActivePageIdx((p) => Math.min(previewDoc.pages!.length - 1, p + 1))}
                  className={`flex items-center space-x-1 px-2 py-0.5 rounded transition-colors ${
                    activePageIdx >= previewDoc.pages.length - 1
                      ? 'text-stone-300 dark:text-stone-700 cursor-not-allowed'
                      : 'text-stone-700 dark:text-stone-200 hover:bg-stone-200 dark:hover:bg-stone-800'
                  }`}
                >
                  <span className="text-[10px]">Next</span>
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Document Preview (Image, PDF iframe, or Text/Doc summary) */}
            {previewDoc.previewUrl && previewDoc.mimeType.startsWith('image/') ? (
              <div className="relative rounded-xl overflow-hidden border border-orange-200/60 dark:border-[#382A20] bg-[#FFFCF9] dark:bg-[#140E0A] p-1 flex items-center justify-center">
                <img
                  src={(previewDoc.pages && previewDoc.pages[activePageIdx]?.url) || previewDoc.previewUrl}
                  alt={previewDoc.name}
                  className="w-full max-h-80 object-contain rounded-lg shadow-sm"
                />
              </div>
            ) : previewDoc.previewUrl && previewDoc.mimeType === 'application/pdf' ? (
              <div className="relative rounded-xl overflow-hidden border border-orange-200/60 dark:border-[#382A20] bg-[#FFFCF9] dark:bg-[#140E0A]">
                <iframe
                  src={previewDoc.previewUrl}
                  title={previewDoc.name}
                  className="w-full h-80 rounded-lg border-0"
                />
              </div>
            ) : (
              <div className="bg-[#FFFCF9] dark:bg-[#140E0A] p-5 rounded-xl border border-orange-200/60 dark:border-[#382A20] text-center space-y-1.5">
                <div className="w-10 h-10 mx-auto rounded-xl bg-white dark:bg-[#1C1510] flex items-center justify-center text-stone-700 dark:text-stone-300 border border-orange-200/60 dark:border-[#382A20]">
                  <FileText className="w-5 h-5" />
                </div>
                <p className="text-xs font-semibold text-stone-900 dark:text-stone-100">{previewDoc.documentType || previewDoc.mimeType}</p>
                {previewDoc.sha256 && (
                  <p className="text-[10px] text-stone-400 font-mono truncate">SHA-256: {previewDoc.sha256.slice(0, 20)}...</p>
                )}
              </div>
            )}

            {/* Multi-Page Quick Pill Switcher */}
            {previewDoc.pages && previewDoc.pages.length > 1 && (
              <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
                {previewDoc.pages.map((page, idx) => (
                  <button
                    key={page.pageNumber || idx}
                    type="button"
                    onClick={() => setActivePageIdx(idx)}
                    className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-[10px] font-mono transition-all ${
                      activePageIdx === idx
                        ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-950 font-bold shadow-xs'
                        : 'bg-stone-100 dark:bg-stone-900 text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white border border-stone-200 dark:border-stone-800'
                    }`}
                  >
                    <span>p.{idx + 1}</span>
                    <span className="opacity-75">{idx === 0 ? '(Front)' : idx === 1 ? '(Back)' : ''}</span>
                  </button>
                ))}
              </div>
            )}

            {/* Summary, Extracted Text & Step-by-Step Security Scan Report */}
            <div className="space-y-2 text-xs max-h-56 overflow-y-auto pr-0.5">
              {previewDoc.originalName && previewDoc.originalName !== previewDoc.name && (
                <p className="text-[10px] text-stone-400">Original filename: {previewDoc.originalName}</p>
              )}

              {/* Summary Box */}
              {(previewDoc.scanReport?.summary || previewDoc.description) && (
                <div className="bg-orange-50/60 dark:bg-[#231912] p-2.5 rounded-lg border border-orange-200/70 dark:border-[#382A20] space-y-1">
                  <div className="flex items-center justify-between text-[10px] font-mono">
                    <span className="font-bold text-orange-700 dark:text-orange-400 uppercase tracking-wider">
                      File / Image Summary
                    </span>
                    {previewDoc.scanReport?.vision_engine && (
                      <span className="px-1.5 py-0.5 rounded bg-white/80 dark:bg-[#16100B] text-stone-500 dark:text-stone-400 border border-orange-200/60 dark:border-stone-700 text-[9px]">
                        {previewDoc.scanReport.vision_engine}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-stone-700 dark:text-stone-200 font-sans leading-relaxed">
                    {previewDoc.scanReport?.summary || previewDoc.description}
                  </p>
                </div>
              )}

              {/* Extracted OCR / Document Text Preview */}
              {previewDoc.scanReport?.extracted_text_preview && (
                <details className="bg-stone-50 dark:bg-[#140E0A] rounded-lg border border-stone-200/70 dark:border-stone-800 p-2">
                  <summary className="cursor-pointer text-[10px] font-mono font-semibold text-stone-600 dark:text-stone-300 select-none">
                    Extracted Text / OCR ({previewDoc.scanReport.extracted_text_preview.length} chars)
                  </summary>
                  <pre className="mt-1.5 text-[10px] text-stone-600 dark:text-stone-400 whitespace-pre-wrap font-mono max-h-28 overflow-y-auto">
                    {previewDoc.scanReport.extracted_text_preview}
                  </pre>
                </details>
              )}

              {/* Step-by-Step Security Scan Report */}
              {previewDoc.scanReport?.steps && previewDoc.scanReport.steps.length > 0 && (
                <div className="bg-stone-50 dark:bg-[#140E0A] p-2.5 rounded-lg border border-stone-200/70 dark:border-stone-800 space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] font-mono">
                    <span className="font-bold text-stone-700 dark:text-stone-200 uppercase tracking-wider">
                      Security & Scan Pipeline
                    </span>
                    {typeof previewDoc.scanReport.total_duration_ms === 'number' && (
                      <span className="text-stone-400">
                        Total: {Math.round(previewDoc.scanReport.total_duration_ms)} ms
                      </span>
                    )}
                  </div>
                  <div className="space-y-1">
                    {previewDoc.scanReport.steps.map((step, i) => (
                      <div key={step.stage || i} className="flex items-start justify-between gap-2 text-[10px] font-mono">
                        <div className="flex items-start gap-1.5 min-w-0">
                          <span
                            className={`mt-0.5 shrink-0 font-bold ${
                              step.status === 'passed'
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-rose-600 dark:text-rose-400'
                            }`}
                          >
                            {step.status === 'passed' ? '✓' : '✗'}
                          </span>
                          <div className="min-w-0">
                            <span className="font-semibold text-stone-800 dark:text-stone-200">{step.label}: </span>
                            <span className="text-stone-500 dark:text-stone-400 break-words">{step.detail}</span>
                          </div>
                        </div>
                        {typeof step.duration_ms === 'number' && step.duration_ms > 0 && (
                          <span className="text-stone-400 shrink-0">{Math.round(step.duration_ms)}ms</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between text-xs text-stone-500 dark:text-stone-400 pt-1 border-t border-stone-100 dark:border-stone-800">
              <span className="text-[11px]">Size: {(previewDoc.size / (1024 * 1024)).toFixed(2)} MB</span>
              {previewDoc.status === 'indexed' ? (
                <div className="flex items-center space-x-2">
                  <button
                    type="button"
                    onClick={() => setSharingDoc(previewDoc)}
                    className="flex items-center space-x-1.5 px-3 py-1.5 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-200 border border-stone-200 dark:border-stone-700 rounded-lg text-xs font-semibold shadow-xs transition-colors cursor-pointer"
                    title="Share document directly (WhatsApp, Mail, AirDrop)"
                  >
                    <Share2 className="w-3.5 h-3.5 text-orange-600 dark:text-orange-400" />
                    <span>Share</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDownloadDocument(previewDoc)}
                    className="flex items-center space-x-1.5 px-3.5 py-1.5 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors cursor-pointer"
                  >
                    <Download className="w-3 h-3" />
                    <span>Download</span>
                  </button>
                </div>
              ) : (
                <span className="text-[10px] font-mono text-rose-500">
                  Access blocked ({previewDoc.scanStatus || previewDoc.status})
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Direct Document Sharing Modal (WhatsApp, Mail, AirDrop, etc. without downloading) */}
      <ShareModal
        document={sharingDoc}
        onClose={() => setSharingDoc(null)}
        activePageIdx={activePageIdx}
      />

      {/* Small floating button to download PWA (Browser Screen Only) */}
      <PwaInstallButton variant="floating" />
    </div>
  );
}

export default App;

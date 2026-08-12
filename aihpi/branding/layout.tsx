"use client";

import React, { Suspense, useState, useRef, useEffect } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import Navbar from "@/components/navbar";
import LoadingScreen from "@/components/common_components/LoadingScreen";
import LegalFooter from "@/components/common_components/LegalFooter";
import LegalBanner from "@/components/common_components/LegalBanner";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { useAuth } from "@/contexts/AuthContext";
import SidebarProvider from "@/app/(dashboard)/components/SidebarProvider";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { DebugWarningBanner } from "@/components/DebugWarningBanner";
import { LicenseExpiryBanner } from "@/components/LicenseExpiryBanner";
import { UserBanner } from "@/components/UserBanner";
import { MIGRATED_PAGES, migratedHref, legacyPageHref, legacyKeyForPathname } from "@/utils/migratedPages";
import { PluginModeProvider, usePluginMode } from "@/contexts/PluginModeContext";
import { createApiClient } from "@/lib/http/client";
import { getProxyBaseUrl } from "@/components/networking";

const pluginApiClient = createApiClient({ getBaseUrl: () => getProxyBaseUrl() ?? "" });

function PluginModeProviderWithAuth({ children }: { children: React.ReactNode }) {
  const { accessToken } = useAuth();
  return <PluginModeProvider accessToken={accessToken}>{children}</PluginModeProvider>;
}

export function AgentControlPlaneView() {
  const { activePlugin } = usePluginMode();
  const activePluginName = activePlugin?.name;
  const agentPlatformUrl = activePlugin?.url ?? "";
  const { accessToken } = useAuth();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [auth, setAuth] = useState<{ plugin: string; claim: string } | null>(null);

  useEffect(() => {
    if (!accessToken || !activePluginName) return;
    let cancelled = false;
    pluginApiClient
      .get("/api/plugins/auth-token", { accessToken, query: { plugin_name: activePluginName } })
      .then((data: { session_claim?: string }) => {
        if (!cancelled && data?.session_claim) setAuth({ plugin: activePluginName, claim: data.session_claim });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accessToken, activePluginName]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe || !auth || auth.plugin !== activePluginName || !agentPlatformUrl) return;
    const send = () => {
      iframe.contentWindow?.postMessage({ type: "litellm-auth", session_claim: auth.claim }, agentPlatformUrl);
    };
    send();
    iframe.addEventListener("load", send);
    return () => iframe.removeEventListener("load", send);
  }, [auth, activePluginName, agentPlatformUrl]);

  if (!agentPlatformUrl) {
    return (
      <div className="flex flex-1 items-center justify-center text-gray-500">
        <div className="text-center">
          <p className="text-lg font-medium mb-2">Plugin</p>
          <p className="text-sm">Configure the plugin URL in settings</p>
        </div>
      </div>
    );
  }

  return (
    <iframe
      ref={iframeRef}
      src={`${agentPlatformUrl.replace(/\/$/, "")}/`}
      style={{
        width: "100%",
        height: "100%",
        border: "none",
        flex: 1,
        minHeight: "calc(100vh - 56px)",
      }}
      title={activePlugin?.display_name ?? "Plugin"}
      allow="clipboard-write"
    />
  );
}

function DashboardShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const { accessToken } = useAuth();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const { mode } = usePluginMode();

  const page = legacyKeyForPathname(pathname) || searchParams.get("page") || "api-keys";
  const isGateway = mode === "ai-gateway";

  const navigateToPage = (newPage: string) => {
    const migratedRoute = MIGRATED_PAGES[newPage];
    router.push(migratedRoute ? migratedHref(migratedRoute) : legacyPageHref(newPage));
  };

  if (!isGateway) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-background">
        <LegalBanner />
        <Navbar accessToken={accessToken} isPublicPage={false} />
        <DebugWarningBanner accessToken={accessToken} />
        <LicenseExpiryBanner accessToken={accessToken} />
        <UserBanner accessToken={accessToken} />
        <main className="flex min-h-0 flex-1 overflow-hidden">
          <AgentControlPlaneView />
        </main>
        <LegalFooter />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <SidebarProvider
          setPage={navigateToPage}
          defaultSelectedKey={page}
          sidebarCollapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <LegalBanner />
          <DashboardHeader page={page} />
          <DebugWarningBanner accessToken={accessToken} />
          <LicenseExpiryBanner accessToken={accessToken} />
          <UserBanner accessToken={accessToken} />
          <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
      <LegalFooter />
    </div>
  );
}

function LayoutContent({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { accessToken, authLoading } = useAuth();
  const isInvitationFlow = Boolean(searchParams.get("invitation_id"));

  useEffect(() => {
    if (!authLoading && isInvitationFlow) {
      router.replace(`${migratedHref("onboarding")}?${searchParams.toString()}`);
    }
  }, [authLoading, isInvitationFlow, router, searchParams]);

  if (authLoading || isInvitationFlow) {
    return <LoadingScreen />;
  }

  return (
    <ThemeProvider accessToken={accessToken}>
      <DashboardShell>{children}</DashboardShell>
    </ThemeProvider>
  );
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <PluginModeProviderWithAuth>
        <LayoutContent>{children}</LayoutContent>
      </PluginModeProviderWithAuth>
    </Suspense>
  );
}

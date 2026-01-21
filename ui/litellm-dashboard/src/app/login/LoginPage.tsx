"use client";

import { useLogin } from "@/app/(dashboard)/hooks/login/useLogin";
import { useUIConfig } from "@/app/(dashboard)/hooks/uiConfig/useUIConfig";
import LoadingScreen from "@/components/common_components/LoadingScreen";
import { getProxyBaseUrl } from "@/components/networking";
import { getCookie } from "@/utils/cookieUtils";
import { isJwtExpired } from "@/utils/jwtUtils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Radio, Space, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getUiAssetPath } from "@/utils/uiAssetPath";

function LoginPageContent() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginMethod, setLoginMethod] = useState<"password" | "ldap">("password");
  const [isLoading, setIsLoading] = useState(true);
  const { data: uiConfig, isLoading: isConfigLoading } = useUIConfig();
  const loginMutation = useLogin();
  const router = useRouter();

  useEffect(() => {
    if (isConfigLoading) {
      return;
    }

    // Check if admin UI is disabled
    if (uiConfig && uiConfig.admin_ui_disabled) {
      setIsLoading(false);
      return;
    }

    const rawToken = getCookie("token");
    if (rawToken && !isJwtExpired(rawToken)) {
      router.replace(`${getProxyBaseUrl()}/ui`);
      return;
    }

    if (uiConfig && uiConfig.auto_redirect_to_sso) {
      router.push(`${getProxyBaseUrl()}/sso/key/generate`);
      return;
    }

    setIsLoading(false);
  }, [isConfigLoading, router, uiConfig]);

  const handleSubmit = () => {
    loginMutation.mutate(
      { username, password, loginMethod },
      {
        onSuccess: (data) => {
          router.push(data.redirect_url);
        },
      },
    );
  };

  const error = loginMutation.error instanceof Error ? loginMutation.error.message : null;
  const isLoginLoading = loginMutation.isPending;

  const { Title, Text, Paragraph } = Typography;

  if (isConfigLoading || isLoading) {
    return <LoadingScreen />;
  }

  // Show disabled message if admin UI is disabled
  if (uiConfig && uiConfig.admin_ui_disabled) {
    return (
      <div className="min-h-screen flex flex-col bg-gray-50">
        <div className="flex flex-1 items-center justify-center">
          <Card className="w-full max-w-lg shadow-md">
            <Space direction="vertical" size="middle" className="w-full">
              <div className="text-center">
                <Title level={2}>AI Model Hub</Title>
                <div className="mt-3 flex items-center justify-center gap-4">
                  <img src={getUiAssetPath("/assets/aisc.png")} alt="AISC" className="h-12 w-auto" />
                  <img
                    src={getUiAssetPath("/assets/BMFTR.png")}
                    alt="BMBF"
                    className="w-auto"
                    style={{ height: "72px" }}
                  />
                </div>
              </div>

              <Alert
                message="Admin UI Disabled"
                description={
                  <>
                    <Paragraph className="text-sm">
                      The Admin UI has been disabled by the administrator. To re-enable it, please update the following
                      environment variable:
                    </Paragraph>
                    <Paragraph className="text-sm">
                      <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">DISABLE_ADMIN_UI=False</code>
                    </Paragraph>
                  </>
                }
                type="warning"
                showIcon
              />
              <div className="text-center text-xs text-gray-500">
                <a
                  href="https://aisc.hpi.de/portal/cfp/pages/imprint/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline"
                >
                  Imprint
                </a>
                <span className="mx-2">•</span>
                <a
                  href="https://aisc.hpi.de/portal/cfp/pages/privacy/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline"
                >
                  Privacy
                </a>
              </div>
            </Space>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <div className="flex flex-1 items-center justify-center">
        <Card className="w-full max-w-lg shadow-md">
          <Space direction="vertical" size="middle" className="w-full">
            <div className="text-center">
              <Title level={2} className="mb-0">
                AI Model Hub
              </Title>
              <Text className="block text-sm text-black-600 mt-0">by KI-Servicezentrum Berlin-Brandenburg</Text>
              <div className="mt-3 flex items-center justify-center gap-4">
                <img src={getUiAssetPath("/assets/aisc.png")} alt="AISC" className="h-12 w-auto" />
                <img
                  src={getUiAssetPath("/assets/BMFTR.png")}
                  alt="BMBF"
                  className="w-auto"
                  style={{ height: "72px" }}
                />
              </div>
            </div>

            {error && <Alert message={error} type="error" showIcon />}

            <Form onFinish={handleSubmit} layout="vertical" requiredMark={true}>
              <Form.Item label={<span className="font-semibold">Login Method</span>} name="login_method">
                <Radio.Group
                  value={loginMethod}
                  onChange={(event) => setLoginMethod(event.target.value)}
                  disabled={isLoginLoading}
                >
                  <Radio value="password">Local</Radio>
                  <Radio value="ldap">AISC Portal</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item
                label="Username"
                name="username"
                rules={[{ required: true, message: "Please enter your username" }]}
              >
                <Input
                  placeholder="Enter your username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={isLoginLoading}
                  size="large"
                  className="rounded-md border-gray-300"
                />
              </Form.Item>

              <Form.Item
                label="Password"
                name="password"
                rules={[{ required: true, message: "Please enter your password" }]}
              >
                <Input.Password
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={isLoginLoading}
                  size="large"
                />
              </Form.Item>

              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={isLoginLoading}
                  disabled={isLoginLoading}
                  block
                  size="large"
                >
                  {isLoginLoading ? "Logging in..." : "Login"}
                </Button>
              </Form.Item>
              <div className="text-center text-xs text-gray-500">
                <a
                  href="https://aisc.hpi.de/portal/cfp/pages/imprint/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline"
                >
                  Imprint
                </a>
                <span className="mx-2">•</span>
                <a
                  href="https://aisc.hpi.de/portal/cfp/pages/privacy/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline"
                >
                  Privacy
                </a>
              </div>
            </Form>
          </Space>
        </Card>
      </div>
    </div>
  );
}

export default function LoginPage() {
  const queryClient = new QueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      <LoginPageContent />
    </QueryClientProvider>
  );
}

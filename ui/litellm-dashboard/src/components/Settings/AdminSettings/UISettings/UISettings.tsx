"use client";

import { useUISettings } from "@/app/(dashboard)/hooks/uiSettings/useUISettings";
import { useUpdateUISettings } from "@/app/(dashboard)/hooks/uiSettings/useUpdateUISettings";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import NotificationManager from "@/components/molecules/notifications_manager";
import { Alert, Button, Card, Form, Input, InputNumber, Skeleton, Space, Switch, Typography } from "antd";

export default function UISettings() {
  const { accessToken } = useAuthorized();
  const { data, isLoading, isError, error } = useUISettings();
  const { mutate: updateSettings, isPending: isUpdating, error: updateError } = useUpdateUISettings(accessToken);

  const schema = data?.field_schema;
  const values = data?.values ?? {};
  const [form] = Form.useForm();
  const properties = schema?.properties ?? {};

  const handleSubmit = (formValues: Record<string, unknown>) => {
    updateSettings(formValues, {
      onSuccess: () => {
        NotificationManager.success("UI settings updated successfully");
      },
      onError: (error) => {
        NotificationManager.fromBackend(error);
      },
    });
  };

  return (
    <Card title="UI Settings">
      {isLoading ? (
        <Skeleton active />
      ) : isError ? (
        <Alert
          type="error"
          message="Could not load UI settings"
          description={error instanceof Error ? error.message : undefined}
        />
      ) : (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          {schema?.description && (
            <Typography.Paragraph style={{ marginBottom: 0 }}>{schema.description}</Typography.Paragraph>
          )}

          {updateError && (
            <Alert
              type="error"
              message="Could not update UI settings"
              description={updateError instanceof Error ? updateError.message : undefined}
            />
          )}

          <Form
            form={form}
            layout="vertical"
            initialValues={values}
            onFinish={handleSubmit}
          >
            <Form.Item
              label="Disable model add for internal users"
              name="disable_model_add_for_internal_users"
              valuePropName="checked"
            >
              <Switch
                disabled={isUpdating}
                loading={isUpdating}
                aria-label={properties?.disable_model_add_for_internal_users?.description}
              />
            </Form.Item>

            <Form.Item
              label="Disable team admin delete team user"
              name="disable_team_admin_delete_team_user"
              valuePropName="checked"
            >
              <Switch
                disabled={isUpdating}
                loading={isUpdating}
                aria-label={properties?.disable_team_admin_delete_team_user?.description}
              />
            </Form.Item>

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              LDAP Settings
            </Typography.Title>

            <Form.Item label="Enable LDAP login" name="ldap_enabled" valuePropName="checked">
              <Switch
                disabled={isUpdating}
                loading={isUpdating}
                aria-label={properties?.ldap_enabled?.description}
              />
            </Form.Item>

            <Form.Item label="LDAP Host" name="ldap_host">
              <Input placeholder="ldap.example.com" disabled={isUpdating} />
            </Form.Item>

            <Form.Item label="LDAP Port" name="ldap_port">
              <InputNumber min={1} max={65535} style={{ width: "100%" }} disabled={isUpdating} />
            </Form.Item>

            <Form.Item label="Use TLS (LDAPS)" name="ldap_use_tls" valuePropName="checked">
              <Switch disabled={isUpdating} loading={isUpdating} aria-label={properties?.ldap_use_tls?.description} />
            </Form.Item>

            <Form.Item label="Base DN" name="ldap_base_dn">
              <Input placeholder="DC=example,DC=com" disabled={isUpdating} />
            </Form.Item>

            <Form.Item label="User Filter" name="ldap_user_filter">
              <Input placeholder="(&(objectClass=user)(mail={username}))" disabled={isUpdating} />
            </Form.Item>

            <Form.Item label="Admin Group DN" name="ldap_admin_group_dn">
              <Input placeholder="CN=litellm-admins,OU=Groups,DC=example,DC=com" disabled={isUpdating} />
            </Form.Item>

            <Form.Item>
              <Button type="primary" htmlType="submit" loading={isUpdating} disabled={isUpdating}>
                Save settings
              </Button>
            </Form.Item>
          </Form>
        </Space>
      )}
    </Card>
  );
}

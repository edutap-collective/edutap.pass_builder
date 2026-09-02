import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { client } from "../api/client";
import { Problem } from "./Tenants";

type ApiClient = {
  id: string;
  name: string;
  scopes: string[];
  active: boolean;
};

/** The four services that fetch a pass, in the order the deployment wires them. */
export const SUGGESTED_CALLERS = [
  "lmu_edutap_backend",
  "lmu_edutap_admin_backend",
  "wallet_apple_vas_account_binding",
  "wallet_apple_vas_web_service",
];

export function ApiClients({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<string | null>(null);

  const clients = useQuery({
    queryKey: ["clients", tenantId],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/tenants/{tenant_id}/clients", {
        params: { path: { tenant_id: tenantId } },
      });
      if (error) throw error;
      return (data ?? []) as ApiClient[];
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await client.POST("/api/v1/tenants/{tenant_id}/clients", {
        params: { path: { tenant_id: tenantId } },
        body: { name, scopes: ["render"] },
      });
      if (error) throw error;
      return data as ApiClient & { token: string };
    },
    onSuccess: (created) => {
      setName("");
      setIssued(created.token);
      void queryClient.invalidateQueries({ queryKey: ["clients", tenantId] });
    },
  });

  const revoke = useMutation({
    mutationFn: async (clientId: string) => {
      const { error } = await client.POST(
        "/api/v1/tenants/{tenant_id}/clients/{client_id}/revoke",
        { params: { path: { tenant_id: tenantId, client_id: clientId } } },
      );
      if (error) throw error;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["clients", tenantId] }),
  });

  return (
    <section>
      <h2>{t("tabs.clients")}</h2>

      {issued ? (
        // Shown once and never again -- only the SHA-256 is stored. The warning
        // sits next to the value rather than in the documentation, because the
        // moment it matters is this one.
        <div role="status" className="issued-token">
          <p>{t("clients.tokenOnce")}</p>
          <code>{issued}</code>
          <button onClick={() => setIssued(null)}>{t("common.close")}</button>
        </div>
      ) : null}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <input
          aria-label={t("clients.name")}
          list="suggested-callers"
          placeholder={t("clients.name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <datalist id="suggested-callers">
          {SUGGESTED_CALLERS.map((caller) => (
            <option key={caller} value={caller} />
          ))}
        </datalist>
        <button type="submit" disabled={create.isPending}>
          {t("clients.create")}
        </button>
      </form>

      {create.error ? <Problem error={create.error} /> : null}
      {revoke.error ? <Problem error={revoke.error} /> : null}

      {clients.data?.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("clients.name")}</th>
              <th>{t("clients.scopes")}</th>
              <th>{t("templates.status")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {clients.data.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.scopes.join(", ")}</td>
                <td>{row.active ? t("clients.active") : t("clients.revoked")}</td>
                <td>
                  {row.active ? (
                    <button onClick={() => revoke.mutate(row.id)}>
                      {t("clients.revoke")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t("clients.empty")}</p>
      )}
    </section>
  );
}

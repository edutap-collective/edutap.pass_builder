import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { API_PREFIX, client } from "../api/client";
import { Problem } from "./Tenants";

type Field = {
  key: string;
  value_type: string;
  label: string | null;
  required: boolean;
  description: string | null;
};

/** Where the designer's copy of the catalogue is fetched from. */
export function catalogueUrl(tenantId: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  return `${base}${API_PREFIX}/tenants/${tenantId}/fields/catalogue.json`;
}

export function Catalogue({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const fields = useQuery({
    queryKey: ["fields", tenantId],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/tenants/{tenant_id}/fields", {
        params: { path: { tenant_id: tenantId } },
      });
      if (error) throw error;
      return (data ?? []) as Field[];
    },
  });

  const refresh = useMutation({
    mutationFn: async () => {
      const { error } = await client.POST("/api/v1/tenants/{tenant_id}/fields/refresh", {
        params: { path: { tenant_id: tenantId } },
      });
      if (error) throw error;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["fields", tenantId] }),
  });

  return (
    <section>
      <h2>{t("tabs.catalogue")}</h2>
      <p className="hint">{t("catalogue.hint")}</p>

      <button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
        {t("catalogue.refresh")}
      </button>{" "}
      <a href={catalogueUrl(tenantId)} download="catalogue.json">
        {t("catalogue.download")}
      </a>

      {refresh.error ? <Problem error={refresh.error} /> : null}

      {fields.error ? <Problem error={fields.error} /> : null}
      {fields.data === undefined ? null : fields.data.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("catalogue.key")}</th>
              <th>{t("catalogue.valueType")}</th>
              <th>{t("catalogue.labelColumn")}</th>
              <th>{t("catalogue.required")}</th>
            </tr>
          </thead>
          <tbody>
            {fields.data.map((field) => (
              <tr key={field.key}>
                <td>
                  <code>{field.key}</code>
                </td>
                <td>{field.value_type}</td>
                <td>{field.label ?? ""}</td>
                <td>{field.required ? "✓" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t("catalogue.empty")}</p>
      )}
    </section>
  );
}

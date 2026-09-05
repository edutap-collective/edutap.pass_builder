import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { client, problemText } from "../api/client";

type Tenant = { id: string; key: string; name: string; active: boolean };

export function useTenants() {
  return useQuery({
    queryKey: ["tenants"],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/tenants", {});
      if (error) throw error;
      return (data ?? []) as Tenant[];
    },
  });
}

/**
 * Picks the tenant every other view works in.
 *
 * It no longer creates one. Tenants are declared in the service's settings and
 * reconciled at startup: a tenant is deployment topology, not something an
 * operator types into a form on a Tuesday. What this shows is the outcome of
 * that reconciliation -- including a tenant that left the settings while it
 * still held rows, which stays listed, marked inactive, and cannot be chosen.
 *
 * The tenant lives in the path rather than in the caller, because a person is
 * not bound to one the way an API token is. Which also means it has to be
 * chosen explicitly -- there is no sensible default beyond "the only one".
 */
export function Tenants({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const tenants = useTenants();

  if (tenants.isLoading) return <p>{t("common.loading")}</p>;

  // A FAILED LOAD MUST NOT LOOK LIKE AN EMPTY ESTATE. Until 2026-09-03
  // `(tenants.data ?? [])` turned a 500 into "no tenant yet", and the first
  // person in front of this interface lost time to exactly that. An error while
  // reading is not an absence of data.
  if (tenants.error) {
    return (
      <div className="tenants">
        <Problem error={tenants.error} />
        <p className="hint">{t("tenant.loadFailed")}</p>
      </div>
    );
  }

  return (
    <div className="tenants">
      <p className="hint">{t("tenant.explain")}</p>
      <label>
        {t("tenant.label")}{" "}
        <select
          value={selected ?? ""}
          onChange={(event) => onSelect(event.target.value)}
        >
          <option value="">{t("tenant.none")}</option>
          {(tenants.data ?? []).map((tenant) => (
            <option key={tenant.id} value={tenant.id} disabled={!tenant.active}>
              {tenant.name} ({tenant.key})
              {tenant.active ? "" : ` — ${t("tenant.inactive")}`}
            </option>
          ))}
        </select>
      </label>

    </div>
  );
}

export function Problem({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <p role="alert" className="problem">
      <strong>{t("common.error")}:</strong> {problemText(error)}
    </p>
  );
}

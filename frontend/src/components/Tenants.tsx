import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { client, problemText } from "../api/client";

type Tenant = { id: string; key: string; name: string };

export function useTenants() {
  return useQuery({
    queryKey: ["tenants"],
    queryFn: async () => {
      const { data, error } = await client.GET("/builder-ui/v1/tenants", {});
      if (error) throw error;
      return (data ?? []) as Tenant[];
    },
  });
}

/**
 * Picks the tenant every other view works in, and creates the first one.
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
  const queryClient = useQueryClient();
  const tenants = useTenants();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await client.POST("/builder-ui/v1/tenants", {
        body: { key, name },
      });
      if (error) throw error;
      return data as Tenant;
    },
    onSuccess: (tenant) => {
      setKey("");
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["tenants"] });
      onSelect(tenant.id);
    },
  });

  if (tenants.isLoading) return <p>{t("common.loading")}</p>;

  return (
    <div className="tenants">
      <label>
        {t("tenant.label")}{" "}
        <select
          value={selected ?? ""}
          onChange={(event) => onSelect(event.target.value)}
        >
          <option value="">{t("tenant.none")}</option>
          {(tenants.data ?? []).map((tenant) => (
            <option key={tenant.id} value={tenant.id}>
              {tenant.name} ({tenant.key})
            </option>
          ))}
        </select>
      </label>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <input
          aria-label={t("tenant.key")}
          placeholder={t("tenant.key")}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <input
          aria-label={t("tenant.name")}
          placeholder={t("tenant.name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <button type="submit" disabled={create.isPending}>
          {t("tenant.create")}
        </button>
      </form>

      {create.error ? <Problem error={create.error} /> : null}
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

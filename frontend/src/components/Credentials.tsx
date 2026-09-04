import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { API_PREFIX, client } from "../api/client";
import type { components } from "../api/schema";
import { JsonEditor } from "./JsonEditor";
import { Problem } from "./Tenants";

type CredentialSet = {
  id: string;
  provider: string;
  label: string;
  status: string;
};

type Mode = "apple-generate" | "apple-import" | "google-import";
type CreateCredentialRequest = components["schemas"]["CreateCredentialRequest"];

/** Where a generated Apple CSR is downloaded from. */
export function csrUrl(tenantId: string, credentialId: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  return `${base}${API_PREFIX}/tenants/${tenantId}/credentials/${credentialId}/csr`;
}

export function Credentials({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("apple-generate");
  const [label, setLabel] = useState("");
  const [commonName, setCommonName] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [certificate, setCertificate] = useState("");
  const [issuerId, setIssuerId] = useState("");
  const [serviceAccount, setServiceAccount] = useState("");

  const credentials = useQuery({
    queryKey: ["credentials", tenantId],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/tenants/{tenant_id}/credentials", {
        params: { path: { tenant_id: tenantId } },
      });
      if (error) throw error;
      return (data ?? []) as CredentialSet[];
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await client.POST("/api/v1/tenants/{tenant_id}/credentials", {
        params: { path: { tenant_id: tenantId } },
        body: buildBody({
          mode,
          label,
          commonName,
          privateKey,
          certificate,
          issuerId,
          serviceAccount,
        }),
      });
      if (error) throw error;
      return data as CredentialSet;
    },
    onSuccess: () => {
      setLabel("");
      setCommonName("");
      setPrivateKey("");
      setCertificate("");
      setIssuerId("");
      setServiceAccount("");
      void queryClient.invalidateQueries({ queryKey: ["credentials", tenantId] });
    },
  });

  return (
    <section>
      <h2>{t("tabs.credentials")}</h2>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <label>
          {t("credentials.provider")}{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="apple-generate">{t("credentials.createApple")}</option>
            <option value="apple-import">{t("credentials.importApple")}</option>
            <option value="google-import">{t("credentials.importGoogle")}</option>
          </select>
        </label>

        <input
          aria-label={t("credentials.label")}
          placeholder={t("credentials.label")}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          required
        />

        {mode === "apple-generate" ? (
          <input
            aria-label={t("credentials.commonName")}
            placeholder={t("credentials.commonName")}
            value={commonName}
            onChange={(e) => setCommonName(e.target.value)}
            required
          />
        ) : null}

        {mode === "apple-import" ? (
          <>
            <textarea
              aria-label={t("credentials.privateKey")}
              placeholder={t("credentials.privateKey")}
              value={privateKey}
              onChange={(e) => setPrivateKey(e.target.value)}
              required
            />
            <textarea
              aria-label={t("credentials.certificate")}
              placeholder={t("credentials.certificate")}
              value={certificate}
              onChange={(e) => setCertificate(e.target.value)}
              required
            />
          </>
        ) : null}

        {mode === "google-import" ? (
          <>
            <input
              aria-label={t("credentials.issuerId")}
              placeholder={t("credentials.issuerId")}
              value={issuerId}
              onChange={(e) => setIssuerId(e.target.value)}
              required
            />
            {/* The service account file runs to a dozen keys, one of which is
                a PEM with escaped newlines in it. As a bare textarea a stray
                character only surfaced on save, as a server-side parse error
                with an offset nobody could map back to a line. */}
            <JsonEditor
              name="service-account.json"
              value={serviceAccount}
              onChange={setServiceAccount}
              rows={10}
              required
            />
          </>
        ) : null}

        <button type="submit" disabled={create.isPending}>
          {t("common.save")}
        </button>
      </form>

      {create.error ? <Problem error={create.error} /> : null}

      {credentials.error ? <Problem error={credentials.error} /> : null}
      {credentials.data === undefined ? null : credentials.data.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("credentials.label")}</th>
              <th>{t("credentials.provider")}</th>
              <th>{t("credentials.status")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {credentials.data.map((row) => (
              <tr key={row.id}>
                <td>{row.label}</td>
                <td>{row.provider}</td>
                <td>{row.status}</td>
                <td>
                  {row.provider === "apple" ? (
                    <a
                      href={csrUrl(tenantId, row.id)}
                      download={`${row.label}.csr`}
                    >
                      {t("credentials.csr")}
                    </a>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t("credentials.empty")}</p>
      )}
    </section>
  );
}

/**
 * Build the request body for the mode in effect.
 *
 * Split out and exported because it is the only part of this form with a
 * decision in it: the service accepts three different shapes on one endpoint
 * and answers `400 invalid_request` for a combination it cannot read, so
 * getting this wrong produces a rejection whose cause is not on the screen.
 *
 * `service_account_json` is parsed here rather than passed as a string: the
 * endpoint takes an object, and a paste with a trailing comma should fail
 * where the person can see it.
 */
export function buildBody(input: {
  mode: Mode;
  label: string;
  commonName: string;
  privateKey: string;
  certificate: string;
  issuerId: string;
  serviceAccount: string;
}): CreateCredentialRequest {
  if (input.mode === "apple-generate") {
    return { provider: "apple", label: input.label, common_name: input.commonName };
  }
  if (input.mode === "apple-import") {
    return {
      provider: "apple",
      label: input.label,
      private_key: input.privateKey,
      certificate: input.certificate,
    };
  }
  return {
    provider: "google",
    label: input.label,
    issuer_id: input.issuerId,
    service_account_json: JSON.parse(input.serviceAccount),
  };
}

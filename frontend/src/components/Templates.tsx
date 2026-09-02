import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { client } from "../api/client";
import type { components } from "../api/schema";
import { Problem } from "./Tenants";

type Template = { id: string; key: string; name: string };
type Variant = {
  id: string;
  key: string;
  name: string;
  wallet_type: string;
  is_default: boolean;
};
type Version = { id: string; number: number; status: string };

export function Templates({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const templates = useQuery({
    queryKey: ["templates", tenantId],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/tenants/{tenant_id}/templates", {
        params: { path: { tenant_id: tenantId } },
      });
      if (error) throw error;
      return (data ?? []) as Template[];
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await client.POST("/api/v1/tenants/{tenant_id}/templates", {
        params: { path: { tenant_id: tenantId } },
        body: { key, name },
      });
      if (error) throw error;
      return data as Template;
    },
    onSuccess: () => {
      setKey("");
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["templates", tenantId] });
    },
  });

  return (
    <section>
      <h2>{t("tabs.templates")}</h2>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <input
          aria-label={t("templates.key")}
          placeholder={t("templates.key")}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <input
          aria-label={t("templates.name")}
          placeholder={t("templates.name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <button type="submit" disabled={create.isPending}>
          {t("templates.create")}
        </button>
      </form>

      {create.error ? <Problem error={create.error} /> : null}

      {templates.data?.length ? (
        <ul className="templates">
          {templates.data.map((template) => (
            <li key={template.id}>
              <button
                onClick={() => setOpen(open === template.id ? null : template.id)}
              >
                {template.name} (<code>{template.key}</code>)
              </button>
              {open === template.id ? (
                <Variants tenantId={tenantId} templateId={template.id} />
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p>{t("templates.empty")}</p>
      )}
    </section>
  );
}

type WalletType = components["schemas"]["WalletType"];
type RuleSpec = components["schemas"]["RuleSpec"];

/**
 * The two this service actually builds.
 *
 * `WalletType` carries ten members -- the shared vocabulary covers Access and
 * Identity too -- and the service answers `501` for the eight it does not
 * build. Offering them here would put a choice on the screen whose only
 * outcome is a refusal.
 */
const WALLET_TYPES: WalletType[] = ["APPLE_VAS", "GOOGLE_ST"];

function Variants({
  tenantId,
  templateId,
}: {
  tenantId: string;
  templateId: string;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [key, setKey] = useState("default");
  const [name, setName] = useState("");
  const [walletType, setWalletType] = useState<WalletType>(WALLET_TYPES[0]);

  const variants = useQuery({
    queryKey: ["variants", templateId],
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/tenants/{tenant_id}/templates/{template_id}/variants",
        { params: { path: { tenant_id: tenantId, template_id: templateId } } },
      );
      if (error) throw error;
      return (data ?? []) as Variant[];
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { error } = await client.POST(
        "/api/v1/tenants/{tenant_id}/templates/{template_id}/variants",
        {
          params: { path: { tenant_id: tenantId, template_id: templateId } },
          body: {
            key,
            name,
            wallet_type: walletType,
            is_default: (variants.data ?? []).length === 0,
          },
        },
      );
      if (error) throw error;
    },
    onSuccess: () => {
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["variants", templateId] });
    },
  });

  return (
    <div className="variants">
      <h3>{t("templates.variants")}</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <input
          aria-label={t("templates.key")}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <input
          aria-label={t("templates.name")}
          placeholder={t("templates.name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <select
          aria-label={t("templates.walletType")}
          value={walletType}
          onChange={(event) => setWalletType(event.target.value as WalletType)}
        >
          {WALLET_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <button type="submit" disabled={create.isPending}>
          {t("templates.addVariant")}
        </button>
      </form>

      {create.error ? <Problem error={create.error} /> : null}

      {(variants.data ?? []).map((variant) => (
        <Versions key={variant.id} tenantId={tenantId} variant={variant} />
      ))}
    </div>
  );
}

function Versions({ tenantId, variant }: { tenantId: string; variant: Variant }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [findings, setFindings] = useState<string[] | null>(null);

  const versions = useQuery({
    queryKey: ["versions", variant.id],
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/tenants/{tenant_id}/variants/{variant_id}/versions",
        { params: { path: { tenant_id: tenantId, variant_id: variant.id } } },
      );
      if (error) throw error;
      return (data ?? []) as Version[];
    },
  });

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ["versions", variant.id] });

  const publish = useMutation({
    mutationFn: async (versionId: string) => {
      const { error } = await client.POST(
        "/api/v1/tenants/{tenant_id}/versions/{version_id}/publish",
        { params: { path: { tenant_id: tenantId, version_id: versionId } } },
      );
      if (error) throw error;
    },
    onSuccess: invalidate,
  });

  const validate = useMutation({
    mutationFn: async (versionId: string) => {
      const { data, error } = await client.POST(
        "/api/v1/tenants/{tenant_id}/versions/{version_id}/validate",
        { params: { path: { tenant_id: tenantId, version_id: versionId } } },
      );
      if (error) throw error;
      return (data as { findings?: string[] } | undefined)?.findings ?? [];
    },
    onSuccess: (result) => setFindings(result),
  });

  return (
    <div className="versions">
      <h4>
        {variant.name} — {variant.wallet_type}
        {variant.is_default ? ` (${t("templates.default")})` : ""}
      </h4>

      <VersionUpload
        tenantId={tenantId}
        variant={variant}
        onCreated={invalidate}
      />

      {publish.error ? <Problem error={publish.error} /> : null}
      {findings ? (
        <p role="status">
          {findings.length ? `• ${findings.join("\n• ")}` : t("templates.valid")}
        </p>
      ) : null}

      <table>
        <tbody>
          {(versions.data ?? []).map((version) => (
            <tr key={version.id}>
              <td>
                {t("templates.version")} {version.number}
              </td>
              <td>{version.status}</td>
              <td>
                <button onClick={() => validate.mutate(version.id)}>
                  {t("templates.validate")}
                </button>{" "}
                {version.status === "draft" ? (
                  <button onClick={() => publish.mutate(version.id)}>
                    {t("templates.publish")}
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VersionUpload({
  tenantId,
  variant,
  onCreated,
}: {
  tenantId: string;
  variant: Variant;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [error, setError] = useState<unknown>(null);
  const isApple = variant.wallet_type.startsWith("APPLE");

  async function uploadBundle(file: File) {
    setError(null);
    const body = new FormData();
    body.append("file", file);
    const { error: failure } = await client.POST(
      "/api/v1/tenants/{tenant_id}/variants/{variant_id}/versions",
      {
        params: { path: { tenant_id: tenantId, variant_id: variant.id } },
        // `body` is a FormData: the endpoint reads the content type to decide
        // which platform path runs, so this must NOT be serialised as JSON.
        body: body as unknown as never,
        bodySerializer: (value: unknown) => value as BodyInit,
      },
    );
    if (failure) setError(failure);
    else onCreated();
  }

  async function importDesigner(files: FileList) {
    setError(null);
    try {
      const trio = await readDesignerTrio(files);
      const { data, error: created } = await client.POST(
        "/api/v1/tenants/{tenant_id}/variants/{variant_id}/versions",
        {
          params: { path: { tenant_id: tenantId, variant_id: variant.id } },
          // The endpoint reads its body off the request rather than
          // declaring one -- the content type decides whether an Apple bundle
          // or a Google payload is being sent -- so the schema documents no
          // body to type this against.
          body: {
            class_json: trio.classJson,
            object_json: trio.objectJson,
          } as unknown as never,
        },
      );
      if (created) throw created;
      const versionId = (data as { id: string }).id;
      if (trio.rules) {
        const { error: bound } = await client.PUT(
          "/api/v1/tenants/{tenant_id}/versions/{version_id}/mappings",
          {
            params: { path: { tenant_id: tenantId, version_id: versionId } },
            body: { rules: trio.rules },
          },
        );
        if (bound) throw bound;
      }
      onCreated();
    } catch (failure) {
      setError(failure);
    }
  }

  return (
    <div className="upload">
      {isApple ? (
        <label>
          {t("templates.uploadBundle")}{" "}
          <input
            type="file"
            accept=".pkpasstemplate,.zip"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void uploadBundle(file);
            }}
          />
        </label>
      ) : (
        <label>
          {t("templates.designerFiles")}{" "}
          <input
            type="file"
            multiple
            accept=".json"
            onChange={(event) => {
              const files = event.target.files;
              if (files?.length) void importDesigner(files);
            }}
          />
        </label>
      )}
      {error ? <Problem error={error} /> : null}
    </div>
  );
}

/**
 * Read the three files the designer exports, whatever order they are picked in.
 *
 * By filename rather than by position: a file picker returns them in whatever
 * order the operating system feels like, and a person selecting three files
 * should not have to know which is first.
 *
 * `mappings.json` carries an `unknown_fields` key alongside `rules`, which the
 * service ignores -- so the file goes over as it stands and nothing has to be
 * rewritten on the way.
 */
export async function readDesignerTrio(files: FileList | File[]): Promise<{
  classJson: unknown;
  objectJson: unknown;
  rules: RuleSpec[] | null;
}> {
  const byName = new Map<string, File>();
  for (const file of Array.from(files)) byName.set(file.name.toLowerCase(), file);

  const read = async (name: string) => {
    const file = byName.get(name);
    return file ? JSON.parse(await file.text()) : null;
  };

  const classJson = await read("class.json");
  const objectJson = await read("object.json");
  if (!classJson || !objectJson) {
    throw { title: "Expected class.json and object.json" };
  }
  const mappings = await read("mappings.json");
  return { classJson, objectJson, rules: mappings?.rules ?? null };
}

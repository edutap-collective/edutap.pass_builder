import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiClients } from "./components/ApiClients";
import { Catalogue } from "./components/Catalogue";
import { Credentials } from "./components/Credentials";
import { Templates } from "./components/Templates";
import { Tenants, useTenants } from "./components/Tenants";

const SECTIONS = ["templates", "credentials", "clients", "catalogue"] as const;
type Section = (typeof SECTIONS)[number];

/** Which cached query holds each section's rows, for the counts in the rail. */
const COUNTED: Record<Section, string> = {
  templates: "templates",
  credentials: "credentials",
  clients: "clients",
  catalogue: "fields",
};

/**
 * Reads a section's row count out of the query cache, without fetching.
 *
 * The rail shows what a tenant already holds, and it must not turn into four
 * more requests on every render to say so. Each section fetches its own rows
 * when opened; this only reports what has already arrived, and shows nothing
 * for what has not. An unknown count is left blank rather than shown as zero --
 * "none yet" and "not looked yet" are different answers, and an operator
 * deciding whether a tenant is configured needs to tell them apart.
 */
function useRowCount(section: Section, tenantId: string | null) {
  const cached = useQueryClient().getQueryData([COUNTED[section], tenantId]);
  return Array.isArray(cached) ? cached.length : null;
}

function Rail({
  tenantId,
  section,
  onSelect,
  onTenants,
}: {
  tenantId: string | null;
  section: Section;
  onSelect: (section: Section) => void;
  onTenants: () => void;
}) {
  const { t } = useTranslation();
  const counts = {
    templates: useRowCount("templates", tenantId),
    credentials: useRowCount("credentials", tenantId),
    clients: useRowCount("clients", tenantId),
    catalogue: useRowCount("catalogue", tenantId),
  };

  return (
    <nav className="rail" aria-label={t("rail.label")}>
      <h2>{t("rail.tenant")}</h2>
      {tenantId ? null : <p className="rail-empty">{t("rail.pickFirst")}</p>}
      <ul>
        {SECTIONS.map((name) => (
          <li key={name}>
            <button
              type="button"
              disabled={!tenantId}
              aria-current={
                tenantId && section === name ? "page" : undefined
              }
              onClick={() => onSelect(name)}
            >
              <span>{t(`tabs.${name}`)}</span>
              {counts[name] === null ? null : (
                <span className="count">{counts[name]}</span>
              )}
            </button>
          </li>
        ))}
      </ul>

      {/* Below the rule sits what is not part of one tenant: the tenants
          themselves. Without it the list becomes unreachable the moment
          somebody picks one, which is a one-way door in a five-item
          interface. */}
      <hr />
      <ul>
        <li>
          <button
            type="button"
            aria-current={tenantId === null ? "page" : undefined}
            onClick={onTenants}
          >
            <span>{t("rail.manageTenants")}</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}

function TopBar({
  tenantId,
  onSelect,
}: {
  tenantId: string | null;
  onSelect: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const tenants = useTenants();
  const templates = useRowCount("templates", tenantId);
  const clients = useRowCount("clients", tenantId);
  const rows = tenants.data ?? [];
  const current = rows.find((tenant) => tenant.id === tenantId);

  return (
    <header className="bar">
      <span className="bar-mark">
        <span className="bar-glyph" aria-hidden="true" />
        eduTAP <em>Builder</em>
      </span>

      <div className="bar-context">
        {/* The tenant switcher lives here rather than in the page, because
            everything below it is scoped to the answer. Somebody who cannot see
            which tenant they are in is one click from editing the wrong one. */}
        {rows.length > 0 ? (
          <>
            <label className="visually-hidden" htmlFor="tenant-switch">
              {t("rail.tenant")}
            </label>
            <select
              id="tenant-switch"
              value={tenantId ?? ""}
              onChange={(event) => onSelect(event.target.value)}
            >
              <option value="" disabled>
                {t("bar.choose")}
              </option>
              {rows.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name}
                </option>
              ))}
            </select>
          </>
        ) : null}

        {current ? (
          <span className="bar-facts">
            <span>
              <b>{current.key}</b>
              {t("bar.key")}
            </span>
            {templates === null ? null : (
              <span>
                <b>{templates}</b>
                {t("tabs.templates")}
              </span>
            )}
            {clients === null ? null : (
              <span>
                <b>{clients}</b>
                {t("tabs.clients")}
              </span>
            )}
          </span>
        ) : null}
      </div>

      <div className="bar-langs">
        {["de", "en"].map((language) => (
          <button
            key={language}
            type="button"
            aria-pressed={i18n.resolvedLanguage === language}
            onClick={() => void i18n.changeLanguage(language)}
          >
            {language.toUpperCase()}
          </button>
        ))}
      </div>
    </header>
  );
}

export function App() {
  const { t } = useTranslation();
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [section, setSection] = useState<Section>("templates");

  return (
    <div className="shell">
      <TopBar tenantId={tenantId} onSelect={setTenantId} />

      <div className="canvas">
        <Rail
          tenantId={tenantId}
          section={section}
          onSelect={setSection}
          onTenants={() => setTenantId(null)}
        />

        <main className="work">
          {tenantId ? (
            <>
              {section === "templates" ? <Templates tenantId={tenantId} /> : null}
              {section === "credentials" ? (
                <Credentials tenantId={tenantId} />
              ) : null}
              {section === "clients" ? <ApiClients tenantId={tenantId} /> : null}
              {section === "catalogue" ? <Catalogue tenantId={tenantId} /> : null}
            </>
          ) : (
            <>
              <h1>{t("app.title")}</h1>
              <p className="lede">{t("app.subtitle")}</p>
              <Tenants selected={tenantId} onSelect={setTenantId} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}

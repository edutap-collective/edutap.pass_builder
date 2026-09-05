import { useMemo } from "react";
import { useTranslation } from "react-i18next";

/**
 * What a Google class and object say about how the pass looks.
 *
 * Deliberately partial. This reads the handful of members that decide the
 * picture and ignores everything else, because the payload also carries
 * scheduling, redemption and analytics that no preview can show. A stricter
 * type here would have to be kept in step with Google's schema for no gain --
 * and would reject a half-typed draft, which is exactly when a preview earns
 * its place.
 */
type Localised = { defaultValue?: { value?: string } };

type PassSource = {
  hexBackgroundColor?: string;
  issuerName?: string;
  programName?: string;
  localizedIssuerName?: Localised;
  logo?: { sourceUri?: { uri?: string } };
  cardTitle?: Localised;
  header?: Localised;
  barcode?: { type?: string; value?: string; alternateText?: string };
  textModulesData?: { header?: string; body?: string; id?: string }[];
};

function text(value: Localised | string | undefined): string | undefined {
  if (typeof value === "string") return value || undefined;
  return value?.defaultValue?.value || undefined;
}

/**
 * Decide whether a background wants light or dark text on it.
 *
 * Relative luminance per WCAG, not the average of the channels: green carries
 * far more perceived brightness than blue, and an average puts white text on
 * backgrounds it disappears against. An issuer choosing their own colour is the
 * normal case here, so this has to hold for whatever they choose.
 */
function readableInk(hex: string): string {
  const value = hex.replace("#", "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value;
  if (!/^[0-9a-f]{6}$/i.test(full)) return "#ffffff";

  const channel = (offset: number) => {
    const srgb = parseInt(full.slice(offset, offset + 2), 16) / 255;
    return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  };
  const luminance =
    0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
  return luminance > 0.45 ? "#14181d" : "#ffffff";
}

function parse(source: string): PassSource | null {
  if (!source.trim()) return null;
  try {
    const parsed: unknown = JSON.parse(source);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as PassSource)
      : null;
  } catch {
    return null;
  }
}

/**
 * Draws the pass a class and object describe, as it would sit in a wallet.
 *
 * Why this is worth having: a class payload is forty lines of nested JSON in
 * which `hexBackgroundColor` is one line. Whether that colour leaves the issuer
 * name legible, whether the card title fits, whether the right three fields
 * ended up on the front -- none of it is answerable by reading. Until now the
 * answer arrived after issuing a real pass to a real phone.
 *
 * It is a rendering of the payload, not of Google's renderer: spacing and type
 * are this interface's, and a wallet will differ in detail. It answers "did I
 * describe the pass I meant", which is the question being asked while editing,
 * and not "is this pixel-exact", which only the platform can answer.
 *
 * The `edutap.pass_designer` service can return a full draft for a class and
 * object pair (`POST /designer/v1/import`), which would carry the field roles
 * this has to guess at. It is not used yet: the browser reaches it only through
 * a path the reverse proxy does not open to XHR, so it would need a proxy route
 * in this service first. The seam is here -- everything below reads from one
 * `PassSource`, whatever produced it.
 */
export function PassPreview({
  classJson,
  objectJson,
}: {
  classJson: string;
  objectJson: string;
}) {
  const { t } = useTranslation();

  const pass = useMemo(() => {
    const klass = parse(classJson);
    const object = parse(objectJson);
    if (!klass && !object) return null;
    // The object wins where both speak: it is the instance, the class is the
    // template behind it.
    return { ...(klass ?? {}), ...(object ?? {}) } as PassSource;
  }, [classJson, objectJson]);

  if (!pass) {
    return (
      <div className="preview-frame">
        <p className="preview-empty">{t("preview.empty")}</p>
      </div>
    );
  }

  const background = /^#?[0-9a-f]{3}(?:[0-9a-f]{3})?$/i.test(
    pass.hexBackgroundColor ?? "",
  )
    ? (pass.hexBackgroundColor as string).startsWith("#")
      ? (pass.hexBackgroundColor as string)
      : `#${pass.hexBackgroundColor}`
    : "#3b4654";

  const ink = readableInk(background);
  const issuer =
    text(pass.localizedIssuerName) ?? pass.issuerName ?? t("preview.noIssuer");
  const title =
    text(pass.cardTitle) ?? pass.programName ?? text(pass.header) ?? "";
  const fields = (pass.textModulesData ?? []).slice(0, 4);

  return (
    <div className="preview-frame">
      <div className="pass" style={{ background, color: ink }}>
        <div className="pass-strip">
          {/* A placeholder block, not the fetched logo: the URI in a draft
              often points at a bucket this browser may not read, and a broken
              image would read as a broken pass. */}
          <span className="pass-logo" aria-hidden="true" />
          <span>{issuer}</span>
        </div>

        {title ? (
          <div className="pass-head">
            <p className="pass-title">{title}</p>
          </div>
        ) : null}

        {fields.length > 0 ? (
          <dl className="pass-fields">
            {fields.map((field, index) => (
              <div className="pass-field" key={field.id ?? index}>
                <dt>{field.header ?? field.id ?? ""}</dt>
                <dd>{field.body ?? ""}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {pass.barcode?.value ? (
          <div className="pass-code">
            <div className="pass-bars" aria-hidden="true" />
            <span>{pass.barcode.alternateText ?? pass.barcode.value}</span>
          </div>
        ) : null}
      </div>

      <p className="preview-note">{t("preview.note")}</p>
    </div>
  );
}

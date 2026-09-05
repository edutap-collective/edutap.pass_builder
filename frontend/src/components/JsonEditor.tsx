import { useId, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

/** One piece of JSON source, classified for colouring. */
type Token = {
  text: string;
  kind: "key" | "string" | "number" | "atom" | "punct" | "plain";
};

/**
 * Split JSON source into coloured tokens, without ever discarding a character.
 *
 * The invariant that matters is not correctness of the grammar but preservation
 * of the text: the highlighted copy sits exactly under an editable textarea, so
 * a dropped or added character moves every glyph after it away from the caret
 * above it. Whatever this does not recognise therefore comes back as `plain`
 * rather than being skipped -- including a half-typed string, which is the
 * normal state of a field somebody is typing into.
 *
 * A key is a string with a colon after it, which is why the lookahead skips
 * whitespace before deciding. Getting that wrong would colour the values of an
 * object the same as its keys and lose the structure a reader scans for.
 */
export function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let index = 0;

  const push = (text: string, kind: Token["kind"]) => {
    if (text) tokens.push({ text, kind });
  };

  while (index < source.length) {
    const rest = source.slice(index);

    const string = /^"(?:[^"\\]|\\.)*"?/.exec(rest);
    if (string) {
      // Only a closed string can be a key, and only if a colon follows it.
      const closed = string[0].length > 1 && string[0].endsWith('"');
      const after = rest.slice(string[0].length);
      const isKey = closed && /^\s*:/.test(after);
      push(string[0], isKey ? "key" : "string");
      index += string[0].length;
      continue;
    }

    const number = /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest);
    if (number) {
      push(number[0], "number");
      index += number[0].length;
      continue;
    }

    const atom = /^(?:true|false|null)\b/.exec(rest);
    if (atom) {
      push(atom[0], "atom");
      index += atom[0].length;
      continue;
    }

    const punct = /^[{}[\],:]+/.exec(rest);
    if (punct) {
      push(punct[0], "punct");
      index += punct[0].length;
      continue;
    }

    // Everything else -- whitespace, newlines, and anything malformed -- in one
    // run, so the character count stays exact.
    const plain = /^(?:[^"\-\d{}[\],:tfn]|[tfn](?!rue|alse|ull)|-(?!\d))+/.exec(
      rest,
    );
    push(plain ? plain[0] : rest[0], "plain");
    index += plain ? plain[0].length : 1;
  }

  return tokens;
}

/** Parse for validity only, returning the message rather than throwing. */
function parseProblem(source: string): string | null {
  if (!source.trim()) return null;
  try {
    JSON.parse(source);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

/**
 * A JSON field with line numbers, syntax colour and a live validity report.
 *
 * Google's class and object payloads run to dozens of lines with nested objects
 * in them. As a bare textarea they were a wall of grey, and a misplaced comma
 * only surfaced on save, as a server-side parse error with an offset nobody
 * could map back to a line.
 */
export function JsonEditor({
  name,
  value,
  onChange,
  rows = 12,
  required = false,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  required?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const gutter = useRef<HTMLDivElement>(null);

  const tokens = useMemo(() => tokenize(value), [value]);
  const problem = useMemo(() => parseProblem(value), [value]);
  const lines = value.split("\n").length;

  return (
    <div className="json">
      <div className="json-head">
        <label className="json-name" htmlFor={id}>
          {name}
        </label>
        <span className="json-state" data-broken={problem !== null}>
          {value.trim() === ""
            ? t("json.empty")
            : problem === null
              ? t("json.valid", { count: lines })
              : t("json.broken")}
        </span>
      </div>

      <div className="json-body">
        {/* Rendered as one text node rather than a list: the numbers must share
            the code's line box exactly, and separate elements pick up their own
            rounding. */}
        <div className="json-gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => i + 1).join("\n")}
        </div>

        <div className="json-code">
          <pre aria-hidden="true">
            {tokens.map((token, i) => (
              <span
                key={i}
                className={token.kind === "plain" ? undefined : `tok-${token.kind}`}
              >
                {token.text}
              </span>
            ))}
            {/* A trailing newline is invisible to the layout, so the last line
                would have no height and the gutter would run one line long. */}
            {"\n"}
          </pre>
          <textarea
            id={id}
            value={value}
            rows={rows}
            required={required}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            onChange={(event) => onChange(event.target.value)}
            onScroll={(event) => {
              // The gutter scrolls with the text it numbers. Without this the
              // numbers stay put and start lying at the first overflow.
              if (gutter.current) {
                gutter.current.scrollTop = event.currentTarget.scrollTop;
              }
            }}
          />
        </div>
      </div>

      {problem ? <p className="problem">{problem}</p> : null}
    </div>
  );
}

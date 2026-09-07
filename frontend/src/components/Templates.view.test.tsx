import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "../i18n";
import { Templates } from "./Templates";

/**
 * Editing the view of a template that already exists.
 *
 * The create form has had the field since #25; the list had none. Whoever
 * forgot the view on creation could not reach it again from the interface --
 * and the first production template, `stwm_rabatt_v1`, was created exactly
 * like that: view NULL, reading `full_view` instead of `mensapass`.
 */
vi.mock("../api/client", () => ({
  API_PREFIX: "/api/v1",
  problemText: (e: unknown) => String(e),
  client: {
    GET: vi.fn(async () => ({
      data: [
        {
          id: "tpl-1",
          key: "stwm_rabatt_v1",
          name: "STWM Rabatt",
          view_type: null,
        },
      ],
      error: undefined,
    })),
    PATCH: vi.fn(async () => ({
      data: {
        id: "tpl-1",
        key: "stwm_rabatt_v1",
        name: "STWM Rabatt",
        view_type: "mensapass",
      },
      error: undefined,
    })),
    POST: vi.fn(),
    PUT: vi.fn(),
  },
}));

import { client } from "../api/client";

function renderTemplates() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Templates tenantId="t1" />
    </QueryClientProvider>,
  );
}

describe("changing a template's view", () => {
  it("patches the view of an existing template", async () => {
    const user = userEvent.setup();
    renderTemplates();

    // The view field of that row -- one per template, labelled for the row.
    // Waiting on the label rather than on the key text: once the field exists,
    // the key appears three times in the row and `findByText` would refuse.
    const field = await screen.findByLabelText(/data view.*stwm_rabatt_v1/i);
    await user.clear(field);
    await user.type(field, "mensapass");
    await user.click(
      screen.getByRole("button", { name: /save view.*stwm_rabatt_v1/i }),
    );

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/v1/tenants/{tenant_id}/templates/{template_id}",
        expect.objectContaining({
          params: { path: { tenant_id: "t1", template_id: "tpl-1" } },
          body: { view_type: "mensapass" },
        }),
      );
    });
  });
});

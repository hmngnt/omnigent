import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SMART_ROUTING_LABEL } from "@/lib/agentLabels";

import {
  MODEL_SELECT_DEFAULT,
  MODEL_SELECT_SMART,
  RoutingModelSelect,
} from "./HarnessConfigControls";

const MODELS = [
  { id: "sonnet", label: "Sonnet 5" },
  { id: "opus", label: "Opus 4.10" },
  { id: "haiku", label: "Haiku 4.5" },
];

function openPicker(testId = "model-picker"): HTMLElement {
  fireEvent.click(screen.getByTestId(testId));
  return screen.getByTestId(testId);
}

describe("RoutingModelSelect", () => {
  it("renders the trigger as a combobox with the resolved label", () => {
    render(
      <RoutingModelSelect
        value="opus"
        onValueChange={vi.fn()}
        offerSmartRouting
        testId="model-picker"
        models={MODELS}
      />,
    );

    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveAttribute("data-testid", "model-picker");
    expect(trigger).toHaveAttribute("aria-label", "Model");
    expect(trigger).toHaveTextContent("Opus 4.10");
  });

  it.each([
    { value: MODEL_SELECT_SMART, expected: SMART_ROUTING_LABEL },
    { value: MODEL_SELECT_DEFAULT, expected: "Default" },
    {
      value: MODEL_SELECT_DEFAULT,
      defaultLabel: "Default (Sonnet 5)",
      expected: "Default (Sonnet 5)",
    },
    { value: "unknown-id", expected: "unknown-id" },
  ])("shows the right trigger label for $value", ({ value, expected, defaultLabel }) => {
    render(
      <RoutingModelSelect
        value={value}
        onValueChange={vi.fn()}
        offerSmartRouting
        testId="model-picker"
        models={MODELS}
        defaultLabel={defaultLabel}
      />,
    );
    expect(screen.getByRole("combobox")).toHaveTextContent(expected);
  });

  it("lists sentinels and models with the correct data attributes", () => {
    render(
      <RoutingModelSelect
        value="opus"
        onValueChange={vi.fn()}
        offerSmartRouting
        testId="model-picker"
        models={MODELS}
        activeModelId="sonnet"
      />,
    );

    openPicker();

    expect(screen.getByRole("option", { name: SMART_ROUTING_LABEL })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Default" })).toBeTruthy();

    const sonnet = screen.getByRole("option", { name: "Sonnet 5" });
    const opus = screen.getByRole("option", { name: "Opus 4.10" });

    expect(sonnet).toHaveAttribute("data-model-id", "sonnet");
    expect(sonnet).toHaveAttribute("data-active", "true");
    expect(opus).toHaveAttribute("data-model-id", "opus");
    expect(opus).not.toHaveAttribute("data-active");
  });

  it("calls onValueChange and closes the dropdown on selection", () => {
    const onValueChange = vi.fn();
    render(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={onValueChange}
        offerSmartRouting
        testId="model-picker"
        models={MODELS}
      />,
    );

    openPicker();
    fireEvent.click(screen.getByRole("option", { name: "Haiku 4.5" }));

    expect(onValueChange).toHaveBeenCalledWith("haiku");
    expect(screen.queryByRole("option", { name: "Haiku 4.5" })).toBeNull();
  });

  it("closes on Escape without selecting", () => {
    const onValueChange = vi.fn();
    render(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={onValueChange}
        offerSmartRouting
        testId="model-picker"
        models={MODELS}
      />,
    );

    openPicker();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

    expect(onValueChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("renders the search input only when the catalog is long", () => {
    const shortModels = Array.from({ length: 15 }, (_, i) => ({
      id: `model-${i}`,
      label: `Model ${i}`,
    }));

    const { rerender } = render(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={vi.fn()}
        offerSmartRouting={false}
        testId="model-picker"
        models={shortModels}
      />,
    );

    openPicker();
    expect(screen.queryByTestId("model-picker-search")).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

    const longModels = Array.from({ length: 16 }, (_, i) => ({
      id: `model-${i}`,
      label: `Model ${i}`,
    }));

    rerender(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={vi.fn()}
        offerSmartRouting={false}
        testId="model-picker"
        models={longModels}
      />,
    );

    openPicker();
    expect(screen.getByTestId("model-picker-search")).toBeTruthy();
  });

  it("filters model items but never the sentinels", () => {
    const longModels = [
      { id: "alpha", label: "Alpha One" },
      { id: "beta", label: "Beta Two" },
      ...Array.from({ length: 20 }, (_, i) => ({
        id: `filler-${i}`,
        label: `Filler ${i}`,
      })),
    ];

    render(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={vi.fn()}
        offerSmartRouting
        testId="model-picker"
        models={longModels}
      />,
    );

    openPicker();
    const search = screen.getByTestId("model-picker-search");
    fireEvent.change(search, { target: { value: "alpha" } });

    expect(screen.getByRole("option", { name: "Alpha One" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Beta Two" })).toBeNull();
    expect(screen.getByRole("option", { name: SMART_ROUTING_LABEL })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Default" })).toBeTruthy();
  });

  it("shows an empty state when the search matches nothing", () => {
    const longModels = Array.from({ length: 20 }, (_, i) => ({
      id: `model-${i}`,
      label: `Model ${i}`,
    }));

    render(
      <RoutingModelSelect
        value={MODEL_SELECT_DEFAULT}
        onValueChange={vi.fn()}
        offerSmartRouting={false}
        testId="model-picker"
        models={longModels}
      />,
    );

    openPicker();
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "zzz" },
    });

    expect(screen.getByText("No models found")).toBeTruthy();
  });
});

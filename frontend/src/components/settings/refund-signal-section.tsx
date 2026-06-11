import { useState } from "react"
import { ReceiptText } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Field,
  linesToList,
  listToLines,
  SettingsCard,
  useSaveSettings,
  type RefundSignalSettings,
} from "@/components/settings/shared"

/** Receipt-parsing labels — the 'refund_signal' settings object. */
export function RefundSignalSection({
  refundSignal,
}: {
  refundSignal: RefundSignalSettings
}) {
  const initial = {
    totalLabel: refundSignal.total_label,
    refundLabel: refundSignal.refund_label,
    cancelledTexts: listToLines(refundSignal.cancelled_texts),
  }
  const [form, setForm] = useState(initial)
  const [baseline, setBaseline] = useState(initial)

  const set = <K extends keyof typeof initial>(field: K, value: string) =>
    setForm((f) => ({ ...f, [field]: value }))

  const dirty = JSON.stringify(form) !== JSON.stringify(baseline)
  const invalid =
    form.totalLabel.trim() === "" || form.refundLabel.trim() === ""
  const save = useSaveSettings("Refund signal")

  function handleSave() {
    save.mutate(
      [
        {
          key: "refund_signal",
          value: {
            ...refundSignal,
            total_label: form.totalLabel.trim(),
            refund_label: form.refundLabel.trim(),
            cancelled_texts: linesToList(form.cancelledTexts),
          },
        },
      ],
      { onSuccess: () => setBaseline(form) },
    )
  }

  return (
    <SettingsCard
      icon={ReceiptText}
      title="Refund signal"
      description="How receipt pages are parsed to decide whether an order was already refunded."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
    >
      <div className="grid gap-5 sm:grid-cols-2">
        <Field
          label="Total label"
          htmlFor="refund-total-label"
          helper="Receipt line that carries the order total."
        >
          <Input
            id="refund-total-label"
            value={form.totalLabel}
            onChange={(e) => set("totalLabel", e.target.value)}
            autoComplete="off"
            aria-invalid={form.totalLabel.trim() === ""}
          />
        </Field>
        <Field
          label="Refund label"
          htmlFor="refund-refund-label"
          helper="Receipt line that carries the refunded amount."
        >
          <Input
            id="refund-refund-label"
            value={form.refundLabel}
            onChange={(e) => set("refundLabel", e.target.value)}
            autoComplete="off"
            aria-invalid={form.refundLabel.trim() === ""}
          />
        </Field>
      </div>

      <Field
        label="Cancelled texts"
        htmlFor="refund-cancelled-texts"
        helper="One per line. Receipt text that marks an order as cancelled."
      >
        <Textarea
          id="refund-cancelled-texts"
          value={form.cancelledTexts}
          onChange={(e) => set("cancelledTexts", e.target.value)}
          className="min-h-20 font-mono text-xs"
          spellCheck={false}
        />
      </Field>
    </SettingsCard>
  )
}

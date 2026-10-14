import { apiFetch } from '../api';
import { useEffect, useState, useCallback } from 'react';

// API response types
interface HeaderFields {
  vendor_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  currency?: string | null;
  subtotal?: string | null;
  total_amount?: string | null;
  tax_amount?: string | null;
  fee_amount?: string | null;
  rounding_adj?: string | null;
}

interface LineItem {
  line_item_id: string;
  sequence_number: number;
  description?: string | null;
  quantity?: string | null;
  unit?: string | null;
  unit_price?: string | null;
  line_total?: string | null;
  // Optional tax fields
  tax_type?: string | null;
  tax_rate?: string | null;
  tax_amount?: string | null;
}

interface PaymentDetails {
  bank_account_number?: string | null;
  bank_name?: string | null;
  payment_reference?: string | null;
}

export interface VoucherPreviewResponse {
  request_id: string;
  status: string;
  authorization_disclaimer: string;
  header_fields?: HeaderFields | null;
  line_items: LineItem[];
  payment_details?: PaymentDetails | null;
}

// API function
const fetchVoucherPreview = async (requestId: string): Promise<VoucherPreviewResponse> => {
  const response = await apiFetch(`/api/requests/${requestId}/voucher-preview`);
  if (!response.ok) {
    throw new Error(`Failed to load voucher preview (HTTP ${response.status})`);
  }
  return response.json();
};

const VoucherPreview = ({ requestId }: { requestId: string }) => {
  const [preview, setPreview] = useState<VoucherPreviewResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchVoucherPreview(requestId);
      setPreview(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    load();
  }, [load]);

  if (isLoading) {
    return (
      <div className="voucher-preview loading" data-testid="voucher-preview-loading">
        <p>Loading voucher preview...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="voucher-preview error" data-testid="voucher-preview-error">
        <p data-testid="voucher-preview-error-message">
          The voucher preview could not be generated. {error}
        </p>
        <button
          className="retry-button"
          data-testid="voucher-preview-retry"
          onClick={load}
        >
          Retry
        </button>
      </div>
    );
  }

  if (!preview) {
    return null;
  }

  const h = preview.header_fields;
  const pd = preview.payment_details;

  return (
    <div className="voucher-preview" data-testid="voucher-preview">

      {/* Header section */}
      <section className="voucher-header" data-testid="voucher-header">
        <h2>Voucher Preview</h2>
        <table className="header-fields-table">
          <tbody>
            {h?.vendor_name != null && (
              <tr>
                <th scope="row">Vendor Name</th>
                <td data-testid="header-vendor-name">{h.vendor_name}</td>
              </tr>
            )}
            {h?.invoice_number != null && (
              <tr>
                <th scope="row">Invoice Number</th>
                <td data-testid="header-invoice-number">{h.invoice_number}</td>
              </tr>
            )}
            {h?.invoice_date != null && (
              <tr>
                <th scope="row">Invoice Date</th>
                <td data-testid="header-invoice-date">{h.invoice_date}</td>
              </tr>
            )}
            {h?.currency != null && (
              <tr>
                <th scope="row">Currency</th>
                <td data-testid="header-currency">{h.currency}</td>
              </tr>
            )}
            {h?.subtotal != null && (
              <tr>
                <th scope="row">Subtotal</th>
                <td data-testid="header-subtotal">{h.subtotal}</td>
              </tr>
            )}
            {h?.total_amount != null && (
              <tr>
                <th scope="row">Total Amount</th>
                <td data-testid="header-total-amount">{h.total_amount}</td>
              </tr>
            )}
            {h?.tax_amount != null && (
              <tr>
                <th scope="row">Tax Amount</th>
                <td data-testid="header-tax-amount">{h.tax_amount}</td>
              </tr>
            )}
            {h?.fee_amount != null && (
              <tr>
                <th scope="row">Fee Amount</th>
                <td data-testid="header-fee-amount">{h.fee_amount}</td>
              </tr>
            )}
            {h?.rounding_adj != null && (
              <tr>
                <th scope="row">Rounding Adjustment</th>
                <td data-testid="header-rounding-adj">{h.rounding_adj}</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {/* Line items section */}
      <section className="voucher-line-items" data-testid="voucher-line-items">
        <h3>Line Items</h3>
        <table className="line-items-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Description</th>
              <th scope="col">Quantity</th>
              <th scope="col">Unit</th>
              <th scope="col">Unit Price</th>
              <th scope="col">Line Total</th>
              <th scope="col">Tax Type</th>
              <th scope="col">Tax Rate</th>
              <th scope="col">Tax Amount</th>
            </tr>
          </thead>
          <tbody>
            {preview.line_items.map((item) => (
              <tr key={item.line_item_id} data-testid={`line-item-${item.sequence_number}`}>
                <td>{item.sequence_number}</td>
                <td data-testid={`line-item-${item.sequence_number}-description`}>
                  {item.description ?? ''}
                </td>
                <td>{item.quantity ?? ''}</td>
                <td>{item.unit ?? ''}</td>
                <td>{item.unit_price ?? ''}</td>
                <td>{item.line_total ?? ''}</td>
                <td data-testid={`line-item-${item.sequence_number}-tax-type`}>
                  {item.tax_type ?? ''}
                </td>
                <td data-testid={`line-item-${item.sequence_number}-tax-rate`}>
                  {item.tax_rate ?? ''}
                </td>
                <td data-testid={`line-item-${item.sequence_number}-tax-amount`}>
                  {item.tax_amount ?? ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Payment details section */}
      {pd && (
        <section className="voucher-payment-details" data-testid="voucher-payment-details">
          <h3>Payment Details</h3>
          <table className="payment-details-table">
            <tbody>
              {pd.bank_account_number != null && (
                <tr>
                  <th scope="row">Bank Account Number</th>
                  <td data-testid="payment-bank-account-number">{pd.bank_account_number}</td>
                </tr>
              )}
              {pd.bank_name != null && (
                <tr>
                  <th scope="row">Bank Name</th>
                  <td data-testid="payment-bank-name">{pd.bank_name}</td>
                </tr>
              )}
              {pd.payment_reference != null && (
                <tr>
                  <th scope="row">Payment Reference</th>
                  <td data-testid="payment-reference">{pd.payment_reference}</td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {/* Authorization disclaimer — prominently displayed (Req 14.2) */}
      <section
        className="voucher-disclaimer"
        data-testid="voucher-disclaimer"
        role="note"
        aria-label="Authorization Disclaimer"
      >
        <h3>Authorization Disclaimer</h3>
        <p data-testid="voucher-disclaimer-text">{preview.authorization_disclaimer}</p>
      </section>
    </div>
  );
};

export default VoucherPreview;

import { useEffect, useState, useRef } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/esm/Page/TextLayer.css';
import 'react-pdf/dist/esm/Page/AnnotationLayer.css';

// Initialize PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.js`;

interface HeaderFields {
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string | null;
  subtotal: string | null;
  total_amount: string | null;
  tax_amount: string | null;
  fee_amount: string | null;
  rounding_adj: string | null;
}

interface LineItem {
  line_item_id: string;
  sequence_number: number;
  description: string | null;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  line_total: string | null;
}

interface PaymentDetails {
  bank_account_number: string | null;
  bank_name: string | null;
  payment_reference: string | null;
}

interface Draft {
  draft_id: string;
  ai_label_visible: boolean;
  extraction_status: string;
  header_fields: HeaderFields;
  line_items: LineItem[];
  payment_details: PaymentDetails | null;
}

interface Request {
  request_id: string;
  client_id: string;
  status: string;
  flags: string[];
  evidence_files: Array<{ file_key: string; original_filename: string }>;
  draft: Draft | null;
}

interface FieldRevision {
  revision_id: string;
  field_path: string;
  old_value: string | null;
  new_value: string | null;
  actor_id: string;
  revised_at: string;
}

interface TelemetryRecord {
  page: string;
  duration_ms: number;
}

// API functions
const fetchRequest = async (requestId: string): Promise<Request> => {
  const response = await fetch(`/api/requests/${requestId}`);
  if (!response.ok) {
    throw new Error('Failed to fetch request');
  }
  return response.json();
};

const fetchEvidenceFile = async (fileKey: string): Promise<Blob> => {
  const response = await fetch(`/api/evidence/${encodeURIComponent(fileKey)}`);
  if (!response.ok) {
    throw new Error('Failed to fetch evidence file');
  }
  return response.blob();
};

const updateField = async (requestId: string, fieldPath: string, newValue: string): Promise<void> => {
  const response = await fetch(`/api/requests/${requestId}/fields`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ field_path: fieldPath, new_value: newValue }),
  });
  if (!response.ok) {
    throw new Error('Failed to update field');
  }
};

const reportTelemetry = async (telemetry: TelemetryRecord): Promise<void> => {
  await fetch('/api/telemetry/page-load', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(telemetry),
  });
};

// Type for unsaved changes
interface UnsavedChanges {
  [fieldPath: string]: string;
}

const ReviewWorkspace = ({ requestId }: { requestId: string }) => {
  const [request, setRequest] = useState<Request | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pdfBlob, setPdfBlob] = useState<Blob | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [unsavedChanges, setUnsavedChanges] = useState<UnsavedChanges>({});
  const [isLeaving, setIsLeaving] = useState(false);
  const [pageLoadDuration, setPageLoadDuration] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [confirmNavigation, setConfirmNavigation] = useState(false);
  
  const mountTimeRef = useRef<number>(0);
  const panelsReadyRef = useRef<boolean>(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load request data
  useEffect(() => {
    mountTimeRef.current = Date.now();
    panelsReadyRef.current = false;

    const loadRequest = async () => {
      try {
        const data = await fetchRequest(requestId);
        setRequest(data);
        setDraft(data.draft);
        setIsLoading(false);

        // Fetch PDF evidence
        if (data.evidence_files && data.evidence_files.length > 0) {
          try {
            const blob = await fetchEvidenceFile(data.evidence_files[0].file_key);
            setPdfBlob(blob);
          } catch (pdfError) {
            setPdfError(`Failed to load PDF: ${pdfError.message}`);
          }
        }

        // Mark panels as ready after data is loaded
        setTimeout(() => {
          if (!panelsReadyRef.current) {
            panelsReadyRef.current = true;
            const duration = Date.now() - mountTimeRef.current;
            setPageLoadDuration(duration);
            
            // Report telemetry
            reportTelemetry({
              page: 'REVIEW_WORKSPACE',
              duration_ms: duration,
            }).catch(err => {
              console.warn('Failed to report telemetry:', err);
            });
          }
        }, 100);
      } catch (error) {
        console.error('Failed to load request:', error);
        setIsLoading(false);
      }
    };

    loadRequest();
  }, [requestId]);

  // Handle navigation with unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (Object.keys(unsavedChanges).length > 0) {
        e.preventDefault();
        e.returnValue = '';
        setConfirmNavigation(true);
        return '';
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [unsavedChanges]);

  const handleFieldChange = (fieldPath: string, value: string) => {
    setUnsavedChanges(prev => ({
      ...prev,
      [fieldPath]: value,
    }));
  };

  const handleFieldBlur = async (fieldPath: string, originalValue: string | null) => {
    const newValue = unsavedChanges[fieldPath];
    
    if (newValue === undefined || newValue === originalValue?.toString()) {
      // No change or empty - remove from unsaved
      setUnsavedChanges(prev => {
        const next = { ...prev };
        delete next[fieldPath];
        return next;
      });
      return;
    }

    try {
      await updateField(requestId, fieldPath, newValue);
      
      // Update draft with new value
      setDraft(prev => {
        if (!prev) return null;
        
        const newDraft = { ...prev };
        
        // Simple path-based update (could be more sophisticated)
        if (fieldPath.startsWith('header.')) {
          const field = fieldPath.split('.')[1];
          newDraft.header_fields = {
            ...newDraft.header_fields,
            [field]: newValue,
          };
        } else if (fieldPath.startsWith('line_items.')) {
          const match = fieldPath.match(/line_items\.(\d+)\.(.+)/);
          if (match) {
            const index = parseInt(match[1], 10);
            const field = match[2];
            if (newDraft.line_items[index]) {
              newDraft.line_items[index] = {
                ...newDraft.line_items[index],
                [field]: newValue,
              };
            }
          }
        } else if (fieldPath.startsWith('payment.')) {
          const field = fieldPath.split('.')[1];
          if (newDraft.payment_details) {
            newDraft.payment_details = {
              ...newDraft.payment_details,
              [field]: newValue,
            };
          }
        }
        
        return newDraft;
      });
      
      // Remove from unsaved changes
      setUnsavedChanges(prev => {
        const next = { ...prev };
        delete next[fieldPath];
        return next;
      });
    } catch (error) {
      console.error('Failed to update field:', error);
      alert('Failed to save changes. Please try again.');
    }
  };

  const handleNavigationConfirm = (proceed: boolean) => {
    if (proceed) {
      setConfirmNavigation(false);
      setIsLeaving(true);
      window.location.reload(); // Simple reload for demo - in real app, use navigation
    } else {
      setConfirmNavigation(false);
    }
  };

  const getFieldValue = (fieldPath: string): string => {
    if (!draft) return '';
    
    if (fieldPath.startsWith('header.')) {
      const field = fieldPath.split('.')[1];
      return draft.header_fields[field as keyof HeaderFields]?.toString() || '';
    } else if (fieldPath.startsWith('line_items.')) {
      const match = fieldPath.match(/line_items\.(\d+)\.(.+)/);
      if (match) {
        const index = parseInt(match[1], 10);
        const field = match[2];
        return draft.line_items[index]?.[field as keyof LineItem]?.toString() || '';
      }
    } else if (fieldPath.startsWith('payment.')) {
      const field = fieldPath.split('.')[1];
      return draft.payment_details?.[field as keyof PaymentDetails]?.toString() || '';
    }
    
    return '';
  };

  const hasUnsavedChanges = Object.keys(unsavedChanges).length > 0;

  if (isLoading) {
    return (
      <div className="review-workspace loading">
        <p>Loading review workspace...</p>
      </div>
    );
  }

  if (isLeaving) {
    return (
      <div className="review-workspace leaving">
        <p>Leaving workspace...</p>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="review-workspace error">
        <p>No draft available for this request.</p>
      </div>
    );
  }

  const renderHeaderFields = () => {
    const fields = [
      { path: 'header.vendor_name', label: 'Vendor Name' },
      { path: 'header.invoice_number', label: 'Invoice Number' },
      { path: 'header.invoice_date', label: 'Invoice Date' },
      { path: 'header.currency', label: 'Currency' },
      { path: 'header.subtotal', label: 'Subtotal' },
      { path: 'header.total_amount', label: 'Total Amount' },
      { path: 'header.tax_amount', label: 'Tax Amount' },
      { path: 'header.fee_amount', label: 'Fee Amount' },
      { path: 'header.rounding_adj', label: 'Rounding Adjustment' },
    ];

    return (
      <div className="header-fields">
        <h3>Header Fields</h3>
        {fields.map(({ path, label }) => {
          const originalValue = getFieldValue(path);
          const unsavedValue = unsavedChanges[path];
          
          return (
            <div key={path} className="field-group">
              <label>{label}</label>
              <div className="field-input-wrapper">
                <input
                  type="text"
                  value={unsavedValue ?? originalValue}
                  onChange={(e) => handleFieldChange(path, e.target.value)}
                  onBlur={() => handleFieldBlur(path, originalValue)}
                  disabled={request?.status === 'AMIGA_REVIEWED' || request?.status === 'SENT_TO_CLIENT' || request?.status === 'REJECTED'}
                />
                <span className="original-value">{originalValue}</span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  const renderLineItems = () => {
    const lineItemFields = [
      { path: 'description', label: 'Description' },
      { path: 'quantity', label: 'Quantity' },
      { path: 'unit', label: 'Unit' },
      { path: 'unit_price', label: 'Unit Price' },
      { path: 'line_total', label: 'Line Total' },
    ];

    return (
      <div className="line-items">
        <h3>Line Items</h3>
        {draft.line_items.map((lineItem, index) => (
          <div key={lineItem.line_item_id} className="line-item-group">
            <h4>Line Item #{index + 1}</h4>
            {lineItemFields.map(({ path, label }) => {
              const fieldPath = `line_items.${index}.${path}`;
              const originalValue = lineItem[path as keyof LineItem]?.toString() || '';
              const unsavedValue = unsavedChanges[fieldPath];
              
              return (
                <div key={fieldPath} className="field-group">
                  <label>{label}</label>
                  <div className="field-input-wrapper">
                    <input
                      type="text"
                      value={unsavedValue ?? originalValue}
                      onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
                      onBlur={() => handleFieldBlur(fieldPath, originalValue)}
                      disabled={request?.status === 'AMIGA_REVIEWED' || request?.status === 'SENT_TO_CLIENT' || request?.status === 'REJECTED'}
                    />
                    <span className="original-value">{originalValue}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    );
  };

  const renderPaymentDetails = () => {
    if (!draft.payment_details) {
      return (
        <div className="payment-details missing">
          <h3>Payment Details</h3>
          <p>No payment details available</p>
        </div>
      );
    }

    const fields = [
      { path: 'payment.bank_account_number', label: 'Bank Account Number' },
      { path: 'payment.bank_name', label: 'Bank Name' },
      { path: 'payment.payment_reference', label: 'Payment Reference' },
    ];

    return (
      <div className="payment-details">
        <h3>Payment Details</h3>
        {fields.map(({ path, label }) => {
          const originalValue = getFieldValue(path);
          const unsavedValue = unsavedChanges[path];
          
          return (
            <div key={path} className="field-group">
              <label>{label}</label>
              <div className="field-input-wrapper">
                <input
                  type="text"
                  value={unsavedValue ?? originalValue}
                  onChange={(e) => handleFieldChange(path, e.target.value)}
                  onBlur={() => handleFieldBlur(path, originalValue)}
                  disabled={request?.status === 'AMIGA_REVIEWED' || request?.status === 'SENT_TO_CLIENT' || request?.status === 'REJECTED'}
                />
                <span className="original-value">{originalValue}</span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="review-workspace" data-testid="review-workspace">
      {/* AI-generated draft label */}
      <div className="ai-label" data-testid="ai-label">
        AI-generated draft — not approved
      </div>

      {/* Two-panel layout */}
      <div className="panels-container">
        {/* PDF Viewer Panel (Left) */}
        <div className="pdf-panel" data-testid="pdf-panel">
          <h3>Invoice PDF</h3>
          {pdfError ? (
            <div className="pdf-error">
              <p>{pdfError}</p>
              {request?.evidence_files && request.evidence_files.length > 0 && (
                <a
                  href={`/requests/evidence/${request.evidence_files[0].file_key}`}
                  download={request.evidence_files[0].original_filename}
                  className="download-link"
                >
                  Download Original File
                </a>
              )}
            </div>
          ) : pdfBlob ? (
            <div className="pdf-viewer">
              <Document
                file={pdfBlob}
                onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                loading="Loading PDF..."
                error="Failed to load PDF"
              >
                {Array.from(new Array(numPages), (el, index) => (
                  <Page key={`page_${index + 1}`} pageNumber={index + 1} scale={1} />
                ))}
              </Document>
            </div>
          ) : (
            <div className="pdf-placeholder">
              <p>No PDF available</p>
            </div>
          )}
        </div>

        {/* Editable Form Panel (Right) */}
        <div className="form-panel" data-testid="form-panel">
          <h3>Edit Draft</h3>
          
          {renderHeaderFields()}
          {renderLineItems()}
          {renderPaymentDetails()}

          {/* Navigation confirmation */}
          {confirmNavigation && (
            <div className="navigation-confirm" data-testid="navigation-confirm">
              <p>You have unsaved changes. Are you sure you want to leave?</p>
              <div className="confirm-buttons">
                <button onClick={() => handleNavigationConfirm(false)}>
                  Stay
                </button>
                <button onClick={() => handleNavigationConfirm(true)}>
                  Leave anyway
                </button>
              </div>
            </div>
          )}

          {/* Status indicators */}
          {hasUnsavedChanges && (
            <div className="unsaved-indicator">
              <span className="dot"></span>
              <span>{Object.keys(unsavedChanges).length} unsaved changes</span>
            </div>
          )}

          <div className="workspace-status">
            <strong>Request Status:</strong> {request?.status}
            {request?.flags && request.flags.length > 0 && (
              <div className="flags">
                <strong>Flags:</strong> {request.flags.join(', ')}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Page load telemetry display (debug) */}
      {pageLoadDuration !== null && (
        <div className="telemetry-debug">
          Page load: {pageLoadDuration}ms
        </div>
      )}
    </div>
  );
};

export default ReviewWorkspace;
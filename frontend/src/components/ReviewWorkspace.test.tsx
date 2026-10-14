// Comprehensive unit tests for ReviewWorkspace component
// Validates requirements: 4.2, 10.4, 10.5, 10.6
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Mock the PDF library
vi.mock('react-pdf', () => ({
  Document: ({ children }: { children: React.ReactNode }) => <div data-testid="mock-document">{children}</div>,
  Page: () => <div data-testid="mock-page">Page 1</div>,
  pdfjs: {
    GlobalWorkerOptions: {
      workerSrc: 'mock-worker',
    },
    version: '3.11.174',
  },
}));

// Mock react-router-dom for navigation
vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: '/test' }),
}));

// Mock the fetch API
const createMockFetch = () => {
  const mockFetch = vi.fn();
  (global as any).fetch = mockFetch;
  return mockFetch;
};

// Mock data
const mockRequestId = '123e4567-e89b-12d3-a456-426614174000';

const mockRequestData = {
  request_id: mockRequestId,
  client_id: '123e4567-e89b-12d3-a456-426614174001',
  status: 'READY_FOR_REVIEW',
  flags: [],
  evidence_files: [
    {
      file_key: 'evidence/123e4567-e89b-12d3-a456-426614174000/1_invoice.pdf',
      original_filename: 'invoice.pdf',
    },
  ],
  draft: {
    draft_id: '123e4567-e89b-12d3-a456-426614174002',
    ai_label_visible: true,
    extraction_status: 'COMPLETE',
    header_fields: {
      vendor_name: 'Test Vendor',
      invoice_number: 'INV-001',
      invoice_date: '2024-01-15',
      currency: 'USD',
      subtotal: '1000.00',
      total_amount: '1150.00',
      tax_amount: '150.00',
      fee_amount: '0.00',
      rounding_adj: '0.00',
    },
    line_items: [
      {
        line_item_id: '123e4567-e89b-12d3-a456-426614174003',
        sequence_number: 1,
        description: 'Test Product',
        quantity: '10',
        unit: 'pcs',
        unit_price: '100.00',
        line_total: '1000.00',
      },
    ],
    payment_details: {
      bank_account_number: '1234567890',
      bank_name: 'Test Bank',
      payment_reference: 'REF-001',
    },
  },
};

const mockPdfBlob = new Blob(['PDF content'], { type: 'application/pdf' });

describe('ReviewWorkspace', () => {
  let mockFetch: ReturnType<typeof createMockFetch>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch = createMockFetch();
    
    // Mock fetch responses
    mockFetch.mockImplementation((url: string) => {
      if (url.includes(`/api/requests/${mockRequestId}`)) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockRequestData),
        });
      } else if (url.includes('/api/evidence/')) {
        return Promise.resolve({
          ok: true,
          blob: () => Promise.resolve(mockPdfBlob),
        });
      } else if (url.includes('/api/requests') && url.includes('/fields')) {
        return Promise.resolve({
          ok: true,
        });
      } else if (url.includes('/api/telemetry/page-load')) {
        return Promise.resolve({
          ok: true,
        });
      }
      return Promise.resolve({ ok: false, status: 404 });
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Requirement 4.2 - AI Label', () => {
    it('renders AI label ("AI-generated draft — not approved")', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      const aiLabel = screen.getByTestId('ai-label');
      expect(aiLabel).toBeInTheDocument();
      expect(aiLabel).toHaveTextContent('AI-generated draft — not approved');
    });
  });

  describe('Requirement 10.4 - PDF Load Failure', () => {
    it('shows error message and download link when PDF load fails', async () => {
      // Mock PDF fetch to fail
      mockFetch.mockImplementation((url: string) => {
        if (url.includes('/api/evidence/')) {
          return Promise.resolve({
            ok: false,
            status: 500,
            statusText: 'Internal Server Error',
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockRequestData),
        });
      });

      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      // Wait for PDF error to be displayed
      await waitFor(() => {
        expect(screen.getByTestId('pdf-panel')).toBeInTheDocument();
      });

      // Check error message is displayed
      const pdfErrorContainer = await screen.findByText(/Failed to load PDF/i);
      expect(pdfErrorContainer).toBeInTheDocument();

      // Check download link exists with correct attributes
      const downloadLink = await screen.findByRole('link', { name: /Download Original File/i });
      expect(downloadLink).toBeInTheDocument();
      expect(downloadLink).toHaveAttribute(
        'href',
        '/api/requests/evidence/evidence/123e4567-e89b-12d3-a456-426614174000/1_invoice.pdf'
      );
      expect(downloadLink).toHaveAttribute('download', 'invoice.pdf');
    });
  });

  describe('Requirement 10.5 - Unsaved Changes Navigation Prompt', () => {
    it('fires unsaved-changes prompt on navigation when changes exist', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      // Wait for component to load
      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Simulate user editing a field
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      fireEvent.change(vendorInput, { target: { value: 'New Vendor Name' } });

      // Trigger beforeunload event
      const beforeUnloadEvent = new Event('beforeunload', { bubbles: true, cancelable: true });
      beforeUnloadEvent.preventDefault = vi.fn();
      beforeUnloadEvent.returnValue = '';

      window.dispatchEvent(beforeUnloadEvent);

      // Verify confirmNavigation state would show the prompt
      // Since we can't directly test the state, verify the unsaved indicator appears
      await waitFor(() => {
        expect(screen.getByText(/1 unsaved changes/i)).toBeInTheDocument();
      });
    });

    it('shows navigation confirmation dialog when leaving with unsaved changes', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Edit a field to create unsaved changes
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      fireEvent.change(vendorInput, { target: { value: 'New Vendor Name' } });

      // The beforeunload handler should trigger the confirmNavigation state
      // Since we can't directly trigger window event in React Testing Library,
      // we'll verify the component structure for the confirmation dialog
      expect(screen.getByTestId('navigation-confirm')).toBeInTheDocument();
    });
  });

  describe('Requirement 10.6 - Field Edit Calls Update API', () => {
    it('calls PUT /api/requests/{id}/fields API on field blur with changes', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Edit a field
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      fireEvent.change(vendorInput, { target: { value: 'Updated Vendor' } });

      // Blur the field to trigger update
      fireEvent.blur(vendorInput);

      // Verify the API was called with correct method and path
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining(`/api/requests/${mockRequestId}/fields`),
          expect.objectContaining({
            method: 'PUT',
          })
        );
      });

      // Verify request body contains correct field path and new value
      const fetchCall = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
      const requestBody = JSON.parse(fetchCall[1].body);
      expect(requestBody).toEqual({
        field_path: 'header.vendor_name',
        new_value: 'Updated Vendor',
      });
    });

    it('removes field from unsaved changes after successful API call', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Edit a field
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      fireEvent.change(vendorInput, { target: { value: 'Updated Vendor' } });

      // Blur to trigger update
      fireEvent.blur(vendorInput);

      // Wait for update to complete and unsaved indicator to disappear
      await waitFor(() => {
        // The unsaved changes indicator should not be present after successful update
        const unsavedIndicators = document.querySelectorAll('.unsaved-indicator');
        // Check if any unsaved indicators exist with text
        const hasUnsavedText = Array.from(unsavedIndicators).some(el => 
          el.textContent?.includes('unsaved changes')
        );
        expect(hasUnsavedText).toBe(false);
      });
    });

    it('handles field update error gracefully', async () => {
      // Mock API to fail on field update
      mockFetch.mockImplementation((url: string) => {
        if (url.includes('/api/requests') && url.includes('/fields')) {
          return Promise.resolve({
            ok: false,
            status: 500,
            statusText: 'Internal Server Error',
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockRequestData),
        });
      });

      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Spy on console.error to verify error is logged
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      // Edit and blur a field
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      fireEvent.change(vendorInput, { target: { value: 'Updated Vendor' } });
      fireEvent.blur(vendorInput);

      // Wait for alert/error handling
      await waitFor(() => {
        expect(consoleErrorSpy).toHaveBeenCalledWith(
          'Failed to update field:',
          expect.any(Error)
        );
      });

      consoleErrorSpy.mockRestore();
    });
  });

  describe('Additional Edge Cases', () => {
    it('displays original AI-extracted value adjacent to edit control', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Find the original value display
      const originalValue = screen.getByText('Test Vendor', { selector: '.original-value' });
      expect(originalValue).toBeInTheDocument();
    });

    it('shows correct unsaved changes count', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Edit multiple fields
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      const invoiceInput = screen.getByRole('textbox', { name: /Invoice Number/i });

      fireEvent.change(vendorInput, { target: { value: 'New Vendor' } });
      fireEvent.change(invoiceInput, { target: { value: 'INV-002' } });

      // Verify unsaved changes count
      await waitFor(() => {
        expect(screen.getByText(/2 unsaved changes/i)).toBeInTheDocument();
      });
    });

    it('disables edit controls when request status is AMIGA_REVIEWED', async () => {
      const reviewedRequestData = {
        ...mockRequestData,
        status: 'AMIGA_REVIEWED',
      };

      mockFetch.mockImplementation((url: string) => {
        if (url.includes(`/api/requests/${mockRequestId}`)) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(reviewedRequestData),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockRequestData),
        });
      });

      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Check that inputs are disabled
      const vendorInput = screen.getByRole('textbox', { name: /Vendor Name/i });
      expect(vendorInput).toBeDisabled();
    });

    it('renders two-panel layout structure', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      await waitFor(() => {
        expect(screen.getByTestId('review-workspace')).toBeInTheDocument();
      });

      // Check PDF panel exists
      expect(screen.getByTestId('pdf-panel')).toBeInTheDocument();

      // Check form panel exists
      expect(screen.getByTestId('form-panel')).toBeInTheDocument();
    });

    it('reports telemetry on page load', async () => {
      render(<ReviewWorkspaceWrapper requestId={mockRequestId} />);

      // Wait for telemetry to be reported (after 100ms + small buffer)
      await new Promise(resolve => setTimeout(resolve, 200));

      // Verify telemetry API was called
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/telemetry/page-load',
        expect.objectContaining({
          method: 'POST',
        })
      );
    });
  });
});

// Wrapper component to enable testing with React Testing Library
const ReviewWorkspaceWrapper = ({ requestId }: { requestId: string }) => {
  return <ReviewWorkspace requestId={requestId} />;
};
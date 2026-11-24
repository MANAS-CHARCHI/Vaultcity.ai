import React, { useState } from 'react';
import { Search, FileCode2, Download, Trash2, Eye, LayoutGrid, List, Edit2, Check, X, Share2, Loader2, ShieldAlert, ShieldCheck, UploadCloud } from 'lucide-react';
import { DocumentFile } from '../types';

interface VaultViewProps {
  documents: DocumentFile[];
  onDeleteDocument: (id: string) => void;
  onPreviewDocument: (doc: DocumentFile) => void;
  onRenameDocument: (id: string, newName: string) => void;
  onShareDocument?: (doc: DocumentFile) => void;
  onDownloadDocument?: (doc: DocumentFile) => void;
  onUploadClick?: () => void;
}

export const VaultView: React.FC<VaultViewProps> = ({
  documents,
  onDeleteDocument,
  onPreviewDocument,
  onRenameDocument,
  onShareDocument,
  onDownloadDocument,
  onUploadClick,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState<string>('');

  const categories = ['All', 'Identity', 'Financial', 'Medical', 'Personal'];

  const filteredDocs = documents.filter((doc) => {
    const matchesSearch =
      doc.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (doc.documentType && doc.documentType.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (doc.description && doc.description.toLowerCase().includes(searchTerm.toLowerCase()));

    const matchesCategory =
      selectedCategory === 'All' || doc.category === selectedCategory;

    return matchesSearch && matchesCategory;
  });

  const startRename = (doc: DocumentFile) => {
    setRenamingId(doc.id);
    setRenameValue(doc.name);
  };

  const handleSaveRename = (id: string) => {
    if (renameValue.trim()) {
      onRenameDocument(id, renameValue.trim());
    }
    setRenamingId(null);
  };

  return (
    <div className="space-y-3.5 pb-28 md:pb-0">
      {/* Top Search, Category & View Controls */}
      <div className="flex flex-col sm:flex-row gap-2.5 items-stretch sm:items-center justify-between pb-1 font-mono">
        {/* Search Input */}
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-stone-400" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="filter vault (/search)..."
            className="w-full bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#2D2118] rounded-lg pl-7 pr-3 py-1.5 text-xs text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:border-orange-500 dark:focus:border-orange-500 transition-colors font-mono"
          />
        </div>

        {/* Categories, Upload & View Switcher */}
        <div className="flex items-center justify-between sm:justify-end gap-2 overflow-x-auto text-xs">
          {onUploadClick && (
            <button
              type="button"
              onClick={onUploadClick}
              className="flex items-center space-x-1 px-2.5 py-1 bg-orange-600 hover:bg-orange-700 text-white dark:bg-orange-500 dark:hover:bg-orange-400 dark:text-stone-950 rounded text-xs font-semibold font-mono transition-all cursor-pointer shrink-0 shadow-xs"
              title="Upload new documents"
            >
              <UploadCloud className="w-3.5 h-3.5" />
              <span>+ upload</span>
            </button>
          )}

          <div className="flex items-center space-x-1">
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-2.5 py-1 rounded text-xs font-mono transition-all ${
                  selectedCategory === cat
                    ? 'bg-orange-600 text-white dark:bg-orange-500 dark:text-stone-950 font-semibold shadow-xs'
                    : 'bg-white dark:bg-[#1C1510] text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 border border-orange-200/60 dark:border-[#2D2118]'
                }`}
              >
                {cat.toLowerCase()}
              </button>
            ))}
          </div>

          <div className="hidden sm:flex items-center bg-orange-100/50 dark:bg-[#1C1510] p-0.5 rounded border border-orange-200/60 dark:border-[#2D2118]">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-1 rounded transition-all ${
                viewMode === 'grid' ? 'bg-white dark:bg-[#281D16] text-stone-900 dark:text-white shadow-sm' : 'text-stone-400'
              }`}
            >
              <LayoutGrid className="w-3 h-3" />
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`p-1 rounded transition-all ${
                viewMode === 'list' ? 'bg-white dark:bg-[#281D16] text-stone-900 dark:text-white shadow-sm' : 'text-stone-400'
              }`}
            >
              <List className="w-3 h-3" />
            </button>
          </div>
        </div>
      </div>

      {/* Document Grid / List without embedded images or colored borders */}
      {filteredDocs.length === 0 ? (
        <div className="py-16 text-center border border-dashed border-orange-200/80 dark:border-[#2D2118] rounded-xl bg-white/40 dark:bg-[#1C1510]/40 font-mono space-y-2.5">
          <FileCode2 className="w-7 h-7 text-stone-300 dark:text-stone-600 mx-auto" />
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {documents.length === 0
              ? 'your vault is empty — upload your first file to scan & store securely'
              : 'no files matching query'}
          </p>
          {documents.length === 0 && onUploadClick && (
            <button
              type="button"
              onClick={onUploadClick}
              className="px-3.5 py-1.5 bg-orange-600 hover:bg-orange-700 text-white dark:bg-orange-500 dark:hover:bg-orange-400 dark:text-stone-950 rounded-lg text-xs font-semibold transition-all cursor-pointer"
            >
              upload document
            </button>
          )}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredDocs.map((doc) => {
            const isReady = doc.status === 'indexed';
            const isScanning = doc.status === 'scanning' || doc.status === 'classifying';
            const isBlocked = doc.status === 'failed';
            return (
            <div
              key={doc.id}
              className="bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#2D2118] hover:border-orange-300 dark:hover:border-[#4A382A] rounded-xl p-4 transition-all flex flex-col justify-between shadow-val hover:shadow-val-hover group"
            >
              <div>
                {/* Vault City Handle Header */}
                <div className="flex items-center justify-between pb-2 mb-2.5 border-b border-stone-100 dark:border-stone-800/80 font-mono text-[10px]">
                  <div className="flex items-center space-x-1.5 min-w-0 flex-1 mr-2 truncate">
                    <span className="text-stone-400 truncate">
                      @vault.city/{doc.category?.toLowerCase() || 'docs'}
                    </span>
                    {doc.pageCount && doc.pageCount > 1 && (
                      <span className="px-1.5 py-0.2 rounded bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-stone-700 text-[9px] font-mono shrink-0">
                        {doc.pageCount} pages
                      </span>
                    )}
                  </div>
                  <div className="flex items-center space-x-1 shrink-0">
                    {isScanning && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-700 dark:text-orange-400 border border-orange-500/20 text-[9px] font-semibold">
                        <Loader2 className="w-2.5 h-2.5 animate-spin" />
                        {doc.status}
                      </span>
                    )}
                    {isBlocked && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-700 dark:text-rose-400 border border-rose-500/20 text-[9px] font-semibold">
                        <ShieldAlert className="w-2.5 h-2.5" />
                        {doc.scanStatus || 'blocked'}
                      </span>
                    )}
                    {isReady && (
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20 text-[9px] font-semibold">
                        <ShieldCheck className="w-2.5 h-2.5" />
                        verified
                      </span>
                    )}
                    {doc.isPii && isReady && (
                      <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/25 font-bold text-[9px]">
                        PII
                      </span>
                    )}
                  </div>
                </div>

                {/* Name & Inline Rename */}
                <div>
                  {renamingId === doc.id ? (
                    <div className="flex items-center space-x-1 my-1">
                      <input
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSaveRename(doc.id)}
                        className="bg-stone-50 dark:bg-stone-900 border border-stone-300 dark:border-stone-600 rounded px-1.5 py-0.5 text-xs text-stone-900 dark:text-white font-mono w-full"
                        autoFocus
                      />
                      <button onClick={() => handleSaveRename(doc.id)} className="p-1 text-emerald-600">
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => setRenamingId(null)} className="p-1 text-stone-400">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <h3
                      onClick={() => onPreviewDocument(doc)}
                      className="text-xs font-semibold text-stone-900 dark:text-white font-mono truncate cursor-pointer hover:text-orange-600 dark:hover:text-orange-400 transition-colors"
                      title="Click to view document"
                    >
                      {doc.name}
                    </h3>
                  )}

                  <p className="text-[10px] text-stone-400 font-mono mt-0.5">
                    {(doc.size / (1024 * 1024)).toFixed(2)} MB • {doc.documentType || 'document'}
                  </p>
                </div>

                {/* AI / OCR Summary shown directly with the file */}
                {doc.description && (
                  <p className="mt-2.5 text-xs text-stone-600 dark:text-stone-300 leading-relaxed font-sans line-clamp-3 break-words">
                    {doc.description}
                  </p>
                )}

                {/* Compact Security & Scan Verification Badges */}
                {doc.scanReport && isReady && (
                  <div className="mt-2 flex flex-wrap items-center gap-1 font-mono text-[9px]">
                    {doc.scanReport.av_engine && (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20">
                        {doc.scanReport.av_engine} ✓
                      </span>
                    )}
                    <span className="px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-700 dark:text-sky-400 border border-sky-500/20">
                      SQLi/PI ✓
                    </span>
                    {typeof doc.scanReport.chunks_stored === 'number' && (
                      <span className="px-1.5 py-0.5 rounded bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-stone-700">
                        {doc.scanReport.chunks_stored} vec
                      </span>
                    )}
                    {typeof doc.scanReport.total_duration_ms === 'number' && (
                      <span className="text-stone-400 ml-0.5">
                        {Math.round(doc.scanReport.total_duration_ms)}ms
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Actions Footer */}
              <div className="mt-3.5 pt-2.5 border-t border-stone-100 dark:border-stone-800/60 flex items-center justify-between font-mono text-[10px] text-stone-400">
                <span>{new Date(doc.uploadedAt).toLocaleDateString()}</span>

                <div className="flex items-center space-x-1">
                  <button
                    onClick={() => startRename(doc)}
                    title="Rename"
                    className="p-1 rounded text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
                  >
                    <Edit2 className="w-3 h-3" />
                  </button>
                  <button
                    onClick={() => isReady && onPreviewDocument(doc)}
                    disabled={!isReady}
                    title={isReady ? 'Preview' : 'Available after security scan completes'}
                    className="p-1 rounded text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                  >
                    <Eye className="w-3 h-3" />
                  </button>
                  <button
                    onClick={() => isReady && onShareDocument?.(doc)}
                    disabled={!isReady}
                    title={isReady ? 'Share directly (WhatsApp, Mail, AirDrop)' : 'Available after security scan completes'}
                    className="p-1 rounded text-stone-400 hover:text-orange-600 dark:hover:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-500/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                  >
                    <Share2 className="w-3 h-3" />
                  </button>
                  <button
                    onClick={() => isReady && onDownloadDocument?.(doc)}
                    disabled={!isReady}
                    title={isReady ? 'Download from Secured S3' : 'Available after security scan completes'}
                    className="p-1 rounded text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                  >
                    <Download className="w-3 h-3" />
                  </button>
                  <button
                    onClick={() => onDeleteDocument(doc.id)}
                    title="Delete"
                    className="p-1 rounded text-stone-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors cursor-pointer"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>
            </div>
            );
          })}
        </div>
      ) : (
        /* List Mode */
        <div className="bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#2D2118] rounded-xl overflow-hidden shadow-val divide-y divide-orange-100 dark:divide-[#2D2118] font-mono">
          {filteredDocs.map((doc) => (
            <div key={doc.id} className="p-3 sm:px-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 hover:bg-orange-50/40 dark:hover:bg-[#281D16]/40 transition-colors text-xs">
              <div className="flex items-start sm:items-center space-x-3 min-w-0 flex-1">
                <FileCode2 className="w-4 h-4 text-stone-400 flex-shrink-0 mt-0.5 sm:mt-0" />
                <div className="min-w-0 flex-1">
                  {renamingId === doc.id ? (
                    <div className="flex items-center space-x-1 max-w-xs">
                      <input
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSaveRename(doc.id)}
                        className="bg-stone-50 dark:bg-stone-900 border border-stone-300 dark:border-stone-600 rounded px-1.5 py-0.5 text-xs text-stone-900 dark:text-white w-full"
                        autoFocus
                      />
                      <button onClick={() => handleSaveRename(doc.id)} className="p-1 text-emerald-600">
                        <Check className="w-3 h-3" />
                      </button>
                      <button onClick={() => setRenamingId(null)} className="p-1 text-stone-400">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <p
                      onClick={() => onPreviewDocument(doc)}
                      className="text-xs font-semibold text-stone-900 dark:text-white truncate cursor-pointer hover:text-orange-600 dark:hover:text-orange-400"
                      title="Click to view document"
                    >
                      {doc.name}
                    </p>
                  )}
                  <p className="text-[10px] text-stone-400 flex flex-wrap items-center gap-1.5 mt-0.5">
                    {doc.pageCount && doc.pageCount > 1 && (
                      <span className="px-1.5 py-0.2 rounded bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-stone-700 text-[9px] font-mono">
                        {doc.pageCount} pages
                      </span>
                    )}
                    <span>{(doc.size / (1024 * 1024)).toFixed(2)} MB</span>
                    <span>•</span>
                    <span>{doc.documentType}</span>
                    {doc.description && (
                      <>
                        <span>•</span>
                        <span className="truncate max-w-xs text-stone-500 dark:text-stone-400 font-sans">{doc.description}</span>
                      </>
                    )}
                  </p>
                </div>
              </div>

              <div className="flex items-center justify-end space-x-1 flex-shrink-0 self-end sm:self-center pt-1.5 sm:pt-0 border-t sm:border-t-0 border-stone-100 dark:border-stone-800/40 w-full sm:w-auto">
                {doc.isPii && (
                  <span className="text-[9px] font-bold text-amber-700 dark:text-amber-300 bg-amber-500/10 px-1.5 py-0.5 rounded mr-1">
                    PII
                  </span>
                )}
                <button onClick={() => startRename(doc)} className="p-1 text-stone-400 hover:text-stone-900 dark:hover:text-white cursor-pointer" title="Rename">
                  <Edit2 className="w-3 h-3" />
                </button>
                <button
                  onClick={() => doc.status === 'indexed' && onPreviewDocument(doc)}
                  disabled={doc.status !== 'indexed'}
                  className="p-1 text-stone-400 hover:text-stone-900 dark:hover:text-white disabled:opacity-30 cursor-pointer"
                  title="Preview"
                >
                  <Eye className="w-3 h-3" />
                </button>
                <button
                  onClick={() => doc.status === 'indexed' && onDownloadDocument?.(doc)}
                  disabled={doc.status !== 'indexed'}
                  className="p-1 text-stone-400 hover:text-stone-900 dark:hover:text-white disabled:opacity-30 cursor-pointer"
                  title="Download"
                >
                  <Download className="w-3 h-3" />
                </button>
                <button
                  onClick={() => doc.status === 'indexed' && onShareDocument?.(doc)}
                  disabled={doc.status !== 'indexed'}
                  className="p-1 text-stone-400 hover:text-orange-600 dark:hover:text-orange-400 disabled:opacity-30 cursor-pointer"
                  title="Share directly"
                >
                  <Share2 className="w-3 h-3" />
                </button>
                <button onClick={() => onDeleteDocument(doc.id)} className="p-1 text-stone-400 hover:text-rose-600 cursor-pointer" title="Delete">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

import React, { useState, useRef } from 'react';
import {
  UploadCloud,
  Camera,
  FileCode2,
  CheckCircle,
  Shield,
  X,
  Edit3,
  Layers,
  Plus,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Split,
  Image as ImageIcon,
} from 'lucide-react';
import { StagedFile, StagedPage } from '../types';

interface UploadViewProps {
  onConfirmUpload: (
    files: {
      file: File;
      finalName: string;
      category: StagedFile['category'];
      pages?: StagedPage[];
    }[]
  ) => Promise<void> | void;
}

export const UploadView: React.FC<UploadViewProps> = ({ onConfirmUpload }) => {
  const [isDragging, setIsDragging] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<StagedFile[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [targetDocIdForPage, setTargetDocIdForPage] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const addPageInputRef = useRef<HTMLInputElement>(null);
  const addPageCameraRef = useRef<HTMLInputElement>(null);

  const handleFilesSelected = (files: FileList | null, isAppendingToTarget = false) => {
    if (!files || files.length === 0) {
      setTargetDocIdForPage(null);
      return;
    }
    setErrorMessage(null);

    // Always clear target doc if doing a standard document addition
    if (!isAppendingToTarget) {
      setTargetDocIdForPage(null);
    }

    // If appending photos to an existing staged document bundle
    if (isAppendingToTarget && targetDocIdForPage) {
      const newPages: StagedPage[] = [];
      let addedSize = 0;

      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (file.size > 25 * 1024 * 1024) {
          setErrorMessage(`"${file.name}" exceeds the 25MB limit.`);
          continue;
        }
        addedSize += file.size;
        const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        newPages.push({
          id: `page-${Date.now()}-${i}`,
          file,
          previewUrl,
          name: file.name,
        });
      }

      if (newPages.length > 0) {
        setStagedFiles((prev) =>
          prev.map((item) => {
            if (item.id === targetDocIdForPage) {
              const currentPages: StagedPage[] = item.pages && item.pages.length > 0
                ? item.pages
                : [{
                    id: `page-${Date.now()}-orig`,
                    file: item.file,
                    previewUrl: item.previewUrl,
                    name: 'Page 1 (Front)',
                  }];
              return {
                ...item,
                pages: [...currentPages, ...newPages],
                size: item.size + addedSize,
              };
            }
            return item;
          })
        );
      }
      setTargetDocIdForPage(null);
      return;
    }

    // Standard new file upload
    const validFiles: StagedFile[] = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];

      // 25MB limit
      if (file.size > 25 * 1024 * 1024) {
        setErrorMessage(`"${file.name}" exceeds the 25MB limit.`);
        continue;
      }

      let previewUrl: string | undefined = undefined;
      if (file.type.startsWith('image/')) {
        previewUrl = URL.createObjectURL(file);
      }

      let defaultCategory: StagedFile['category'] = 'Other';
      const lower = file.name.toLowerCase();
      if (lower.includes('aadhaar') || lower.includes('passport') || lower.includes('id') || lower.includes('license')) {
        defaultCategory = 'Identity';
      } else if (lower.includes('bank') || lower.includes('salary') || lower.includes('tax') || lower.includes('bill')) {
        defaultCategory = 'Financial';
      } else if (lower.includes('med') || lower.includes('prescrip') || lower.includes('report') || lower.includes('lab')) {
        defaultCategory = 'Medical';
      } else if (file.type.startsWith('image/')) {
        defaultCategory = 'Personal';
      }

      const isImage = file.type.startsWith('image/');
      const initialPages: StagedPage[] = isImage
        ? [
            {
              id: `page-${Date.now()}-${i}`,
              file,
              previewUrl,
              name: 'Page 1 (Front)',
            },
          ]
        : [];

      validFiles.push({
        id: `staged-${Date.now()}-${i}`,
        file,
        defaultName: file.name,
        customName: file.name,
        category: defaultCategory,
        size: file.size,
        previewUrl,
        pages: initialPages,
      });
    }

    if (validFiles.length > 0) {
      setStagedFiles((prev) => [...prev, ...validFiles]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFilesSelected(e.dataTransfer.files);
    }
  };

  const handleNameChange = (id: string, newName: string) => {
    setStagedFiles((prev) =>
      prev.map((item) => (item.id === id ? { ...item, customName: newName } : item))
    );
  };

  const handleCategoryChange = (id: string, category: StagedFile['category']) => {
    setStagedFiles((prev) =>
      prev.map((item) => (item.id === id ? { ...item, category } : item))
    );
  };

  const removeStagedFile = (id: string) => {
    setStagedFiles((prev) => {
      const removed = prev.find((item) => item.id === id);
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      removed?.pages?.forEach((p) => {
        if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      });
      return prev.filter((item) => item.id !== id);
    });
  };

  // Trigger adding an extra page to a specific staged document
  const triggerAddPageToFile = (docId: string, useCamera = false) => {
    setTargetDocIdForPage(docId);
    if (useCamera) {
      addPageCameraRef.current?.click();
    } else {
      addPageInputRef.current?.click();
    }
  };

  // Move page position (left / right)
  const movePage = (docId: string, index: number, direction: 'left' | 'right') => {
    setStagedFiles((prev) =>
      prev.map((item) => {
        if (item.id !== docId || !item.pages) return item;
        const newIndex = direction === 'left' ? index - 1 : index + 1;
        if (newIndex < 0 || newIndex >= item.pages.length) return item;

        const updatedPages = [...item.pages];
        const temp = updatedPages[index];
        updatedPages[index] = updatedPages[newIndex];
        updatedPages[newIndex] = temp;

        return {
          ...item,
          file: updatedPages[0].file,
          previewUrl: updatedPages[0].previewUrl,
          pages: updatedPages,
        };
      })
    );
  };

  // Remove a specific page from a multi-page document
  const removePage = (docId: string, pageId: string) => {
    setStagedFiles((prev) =>
      prev
        .map((item) => {
          if (item.id !== docId || !item.pages) return item;
          const updatedPages = item.pages.filter((p) => p.id !== pageId);
          if (updatedPages.length === 0) return null; // remove whole doc

          const removedPage = item.pages.find((p) => p.id === pageId);
          const newSize = Math.max(0, item.size - (removedPage?.file.size || 0));

          return {
            ...item,
            file: updatedPages[0].file,
            previewUrl: updatedPages[0].previewUrl,
            pages: updatedPages,
            size: newSize,
          };
        })
        .filter((item): item is StagedFile => item !== null)
    );
  };

  // One-click bundle: merge multiple staged photos into 1 document
  const bundleAllImagesIntoOne = () => {
    const imageItems = stagedFiles.filter((item) => item.pages && item.pages.length > 0);
    if (imageItems.length < 2) return;

    const allPages: StagedPage[] = [];
    let totalSize = 0;
    let preferredCategory: StagedFile['category'] = 'Identity';

    imageItems.forEach((item, docIdx) => {
      if (item.category !== 'Other') preferredCategory = item.category;
      totalSize += item.size;
      if (item.pages && item.pages.length > 0) {
        item.pages.forEach((page, pIdx) => {
          allPages.push({
            ...page,
            id: `bundle-page-${Date.now()}-${docIdx}-${pIdx}`,
            name: page.name || `Page ${allPages.length + 1}`,
          });
        });
      } else {
        allPages.push({
          id: `bundle-page-${Date.now()}-${docIdx}`,
          file: item.file,
          previewUrl: item.previewUrl,
          name: item.defaultName,
        });
      }
    });

    const nonImageItems = stagedFiles.filter((item) => !item.pages || item.pages.length === 0);

    const bundledDoc: StagedFile = {
      id: `bundle-${Date.now()}`,
      file: allPages[0].file,
      defaultName: 'document_bundle.pdf',
      customName: 'document_bundle.pdf',
      category: preferredCategory,
      size: totalSize,
      previewUrl: allPages[0].previewUrl,
      pages: allPages,
    };

    setStagedFiles([...nonImageItems, bundledDoc]);
  };

  // Split a bundled document back into separate individual items
  const unbundleDocument = (docId: string) => {
    const targetDoc = stagedFiles.find((d) => d.id === docId);
    if (!targetDoc || !targetDoc.pages || targetDoc.pages.length <= 1) return;

    const splitItems: StagedFile[] = targetDoc.pages.map((p, idx) => ({
      id: `split-${Date.now()}-${idx}`,
      file: p.file,
      defaultName: p.name || `page_${idx + 1}.jpg`,
      customName: p.name || `page_${idx + 1}.jpg`,
      category: targetDoc.category,
      size: p.file.size,
      previewUrl: p.previewUrl,
      pages: [p],
    }));

    setStagedFiles((prev) =>
      prev.flatMap((item) => (item.id === docId ? splitItems : [item]))
    );
  };

  const handleFinalSave = async () => {
    if (stagedFiles.length === 0 || isUploading) return;
    setErrorMessage(null);
    setIsUploading(true);

    const payload = stagedFiles.map((item) => ({
      file: item.file,
      finalName: item.customName.trim() || item.defaultName,
      category: item.category,
      pages: item.pages && item.pages.length > 1 ? item.pages : undefined,
    }));

    try {
      await onConfirmUpload(payload);
      setStagedFiles([]);
      setTargetDocIdForPage(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (cameraInputRef.current) cameraInputRef.current.value = '';
      if (addPageInputRef.current) addPageInputRef.current.value = '';
      if (addPageCameraRef.current) addPageCameraRef.current.value = '';
    } catch (err: any) {
      setErrorMessage(err?.message || 'Upload failed. Please try again.');
    } finally {
      setIsUploading(false);
    }
  };

  const imageItemsCount = stagedFiles.filter((item) => item.pages && item.pages.length > 0).length;

  return (
    <div className="max-w-xl mx-auto space-y-3.5 font-mono">
      <div className="text-center space-y-0.5">
        <h2 className="text-sm font-semibold text-stone-900 dark:text-white">upload to vault</h2>
        <p className="text-[11px] text-stone-500 dark:text-stone-400">
          Private &amp; encrypted • Multi-photo document bundling &amp; preservation
        </p>
      </div>

      {errorMessage && (
        <div className="p-2 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-700 dark:text-rose-300 text-xs flex items-center justify-between">
          <span>{errorMessage}</span>
          <button onClick={() => setErrorMessage(null)}>
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Hidden inputs — all support multi-file selection and clean reset */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="*/*"
        className="hidden"
        onChange={(e) => {
          handleFilesSelected(e.target.files);
          e.target.value = '';
        }}
      />

      <input
        ref={cameraInputRef}
        type="file"
        multiple
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          handleFilesSelected(e.target.files);
          e.target.value = '';
        }}
      />

      {/* Inputs for adding additional page / back side to an existing staged card */}
      <input
        ref={addPageInputRef}
        type="file"
        multiple
        accept="image/*,application/pdf"
        className="hidden"
        onChange={(e) => {
          handleFilesSelected(e.target.files, true);
          e.target.value = '';
        }}
      />

      <input
        ref={addPageCameraRef}
        type="file"
        multiple
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          handleFilesSelected(e.target.files, true);
          e.target.value = '';
        }}
      />

      {stagedFiles.length === 0 ? (
        <div className="space-y-2.5">
          {/* Main Drop Area */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-7 flex flex-col items-center justify-center transition-all cursor-pointer ${
              isDragging
                ? 'border-orange-500 dark:border-orange-400 bg-orange-100/50 dark:bg-[#281D16]'
                : 'border-orange-200/80 dark:border-[#2D2118] hover:border-orange-400 dark:hover:border-[#4A382A] bg-white dark:bg-[#1C1510] shadow-val'
            }`}
          >
            <div className="w-8 h-8 rounded-lg bg-orange-50 dark:bg-[#251A12] flex items-center justify-center text-orange-600 dark:text-orange-400 mb-2 border border-orange-200/60 dark:border-[#382A20]">
              <UploadCloud className="w-4 h-4" />
            </div>
            <p className="text-xs font-semibold text-stone-800 dark:text-stone-200 text-center">
              &gt; drag & drop files or multiple photos, or <span className="underline cursor-pointer">browse</span>
            </p>
            <p className="text-[10px] text-stone-400 mt-1">
              Supports multi-page documents (Front &amp; Back photos) • PDF, JPG, PNG (max 10MB)
            </p>
          </div>

          {/* Quick Choice Action Grid */}
          <div className="grid grid-cols-2 gap-2.5">
            <button
              type="button"
              onClick={() => cameraInputRef.current?.click()}
              className="flex items-center space-x-2.5 p-3 bg-white dark:bg-[#1C1510] hover:bg-emerald-50/40 dark:hover:bg-emerald-950/20 border border-orange-200/70 dark:border-[#2D2118] hover:border-emerald-500/40 rounded-xl text-xs font-medium text-stone-800 dark:text-stone-200 shadow-val transition-all cursor-pointer active:scale-95 text-left group"
            >
              <div className="w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center flex-shrink-0 group-hover:scale-105 transition-transform">
                <Camera className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-xs text-stone-900 dark:text-white">Camera Capture</p>
                <p className="text-[10px] text-stone-400">Scan front &amp; back sides</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center space-x-2.5 p-3 bg-white dark:bg-[#1C1510] hover:bg-orange-50/40 dark:hover:bg-orange-950/20 border border-orange-200/70 dark:border-[#2D2118] hover:border-orange-500/40 rounded-xl text-xs font-medium text-stone-800 dark:text-stone-200 shadow-val transition-all cursor-pointer active:scale-95 text-left group"
            >
              <div className="w-7 h-7 rounded-lg bg-orange-500/10 text-orange-600 dark:text-orange-400 flex items-center justify-center flex-shrink-0 group-hover:scale-105 transition-transform">
                <FileCode2 className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-xs text-stone-900 dark:text-white">Browse Files</p>
                <p className="text-[10px] text-stone-400">Select single or multiple photos</p>
              </div>
            </button>
          </div>
        </div>
      ) : (
        /* Staging Screen in Val Town file format */
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          className={`border rounded-xl p-3.5 shadow-val space-y-3 font-mono transition-all ${
            isDragging
              ? 'border-orange-500 dark:border-orange-400 bg-orange-100/50 dark:bg-[#281D16]'
              : 'bg-white dark:bg-[#1C1510] border-orange-200/80 dark:border-[#2D2118]'
          }`}
        >
          <div className="flex items-center justify-between pb-2 border-b border-stone-100 dark:border-stone-800">
            <div>
              <h3 className="text-xs font-bold text-stone-900 dark:text-white">staged documents for upload</h3>
              <p className="text-[10px] text-stone-400">drag & drop or browse more files anytime</p>
            </div>
            <div className="flex items-center space-x-2">
              {imageItemsCount >= 2 && (
                <button
                  type="button"
                  onClick={bundleAllImagesIntoOne}
                  className="flex items-center space-x-1 text-[10px] bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded hover:bg-emerald-500/20 transition-colors cursor-pointer"
                >
                  <Sparkles className="w-3 h-3" />
                  <span>bundle into 1 document</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center space-x-1 text-[10px] bg-orange-500/10 text-orange-700 dark:text-orange-400 border border-orange-500/20 px-2 py-0.5 rounded hover:bg-orange-500/20 transition-colors font-semibold cursor-pointer"
              >
                <Plus className="w-3 h-3" />
                <span>+ browse more</span>
              </button>
              <span className="text-[10px] bg-stone-100 dark:bg-stone-800 px-2 py-0.5 rounded text-stone-600 dark:text-stone-300">
                {stagedFiles.length} item{stagedFiles.length > 1 ? 's' : ''}
              </span>
            </div>
          </div>

          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {stagedFiles.map((item) => {
              const isMultiPage = item.pages && item.pages.length > 1;
              const hasPages = item.pages && item.pages.length > 0;

              return (
                <div
                  key={item.id}
                  className="p-3 bg-white dark:bg-[#1C1510] rounded-lg border border-orange-200/80 dark:border-[#2D2118] space-y-2.5"
                >
                  {/* Header Row: Type, Name, Size & Delete */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2 min-w-0">
                      <div className="w-7 h-7 rounded bg-orange-50/60 dark:bg-[#251A12] flex items-center justify-center text-stone-700 dark:text-stone-300 border border-orange-200/60 dark:border-[#382A20] shrink-0">
                        {isMultiPage ? (
                          <Layers className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        ) : item.previewUrl ? (
                          <ImageIcon className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                        ) : (
                          <FileCode2 className="w-3.5 h-3.5 text-stone-500" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center space-x-1.5">
                          <p className="text-[11px] font-semibold text-stone-900 dark:text-white truncate">
                            {item.customName}
                          </p>
                          {isMultiPage && (
                            <span className="text-[9px] bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 px-1.5 py-0.2 rounded border border-emerald-500/20 font-bold shrink-0">
                              {item.pages?.length} pages
                            </span>
                          )}
                        </div>
                        <p className="text-[10px] text-stone-400">
                          Total size: {(item.size / (1024 * 1024)).toFixed(2)} MB
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center space-x-1">
                      {isMultiPage && (
                        <button
                          type="button"
                          onClick={() => unbundleDocument(item.id)}
                          title="Split into individual files"
                          className="p-1 text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 transition-colors"
                        >
                          <Split className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => removeStagedFile(item.id)}
                        className="p-1 text-stone-400 hover:text-rose-600 transition-colors"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Multi-Photo Pages Strip (The core requested feature!) */}
                  {hasPages && (
                    <div className="space-y-1.5 pt-1">
                      <div className="flex items-center justify-between text-[10px] text-stone-500">
                        <span className="flex items-center space-x-1">
                          <Layers className="w-3 h-3 text-stone-400" />
                          <span>Document Pages ({item.pages?.length || 1})</span>
                        </span>
                        <span className="text-[9px] text-stone-400">drag or move to reorder</span>
                      </div>

                      <div className="flex items-center gap-2 overflow-x-auto pb-1">
                        {item.pages?.map((page, idx) => (
                          <div
                            key={page.id}
                            className="relative group shrink-0 w-24 bg-white dark:bg-[#1C1510] border border-orange-200/70 dark:border-[#382A20] rounded-lg p-1.5 space-y-1"
                          >
                            {/* Page thumbnail */}
                            <div className="w-full h-16 rounded overflow-hidden bg-stone-100 dark:bg-stone-900 border border-stone-100 dark:border-stone-800 flex items-center justify-center">
                              {page.previewUrl ? (
                                <img
                                  src={page.previewUrl}
                                  alt={page.name}
                                  className="w-full h-full object-cover"
                                />
                              ) : (
                                <FileCode2 className="w-4 h-4 text-stone-400" />
                              )}
                            </div>

                            {/* Page Label & Index */}
                            <div className="flex items-center justify-between text-[9px]">
                              <span className="font-semibold text-stone-700 dark:text-stone-300 truncate max-w-[50px]">
                                p.{idx + 1} {idx === 0 ? '(front)' : idx === 1 ? '(back)' : ''}
                              </span>
                              {item.pages && item.pages.length > 1 && (
                                <button
                                  type="button"
                                  onClick={() => removePage(item.id, page.id)}
                                  className="text-stone-400 hover:text-rose-500"
                                >
                                  <X className="w-2.5 h-2.5" />
                                </button>
                              )}
                            </div>

                            {/* Move controls */}
                            {item.pages && item.pages.length > 1 && (
                              <div className="flex items-center justify-between pt-0.5 border-t border-stone-100 dark:border-stone-800">
                                <button
                                  type="button"
                                  disabled={idx === 0}
                                  onClick={() => movePage(item.id, idx, 'left')}
                                  className={`p-0.5 rounded ${
                                    idx === 0 ? 'text-stone-300 dark:text-stone-700 cursor-not-allowed' : 'text-stone-500 hover:text-stone-900 dark:hover:text-white'
                                  }`}
                                >
                                  <ChevronLeft className="w-2.5 h-2.5" />
                                </button>
                                <span className="text-[8px] text-stone-400 font-mono">{idx + 1}/{item.pages.length}</span>
                                <button
                                  type="button"
                                  disabled={idx === item.pages.length - 1}
                                  onClick={() => movePage(item.id, idx, 'right')}
                                  className={`p-0.5 rounded ${
                                    idx === item.pages.length - 1 ? 'text-stone-300 dark:text-stone-700 cursor-not-allowed' : 'text-stone-500 hover:text-stone-900 dark:hover:text-white'
                                  }`}
                                >
                                  <ChevronRight className="w-2.5 h-2.5" />
                                </button>
                              </div>
                            )}
                          </div>
                        ))}

                        {/* "+ Add Another Page" Slot */}
                        <div className="shrink-0 flex flex-col items-center justify-center w-24 h-[94px] border border-dashed border-stone-300 dark:border-stone-700 rounded-lg bg-stone-100/50 dark:bg-stone-900/40 hover:bg-stone-100 dark:hover:bg-stone-900 transition-colors p-1.5 space-y-1">
                          <p className="text-[9px] text-stone-500 font-semibold text-center">
                            + add side / page
                          </p>
                          <div className="flex items-center space-x-1">
                            <button
                              type="button"
                              onClick={() => triggerAddPageToFile(item.id, false)}
                              title="Browse extra photo"
                              className="p-1 rounded bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300 hover:text-stone-950 border border-stone-200 dark:border-stone-700 shadow-xs"
                            >
                              <Plus className="w-3 h-3" />
                            </button>
                            <button
                              type="button"
                              onClick={() => triggerAddPageToFile(item.id, true)}
                              title="Camera scan next page"
                              className="p-1 rounded bg-white dark:bg-stone-800 text-emerald-600 dark:text-emerald-400 hover:text-emerald-500 border border-stone-200 dark:border-stone-700 shadow-xs"
                            >
                              <Camera className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Document Name Field */}
                  <div>
                    <label className="block text-[10px] text-stone-500 mb-0.5 flex items-center">
                      <Edit3 className="w-3 h-3 mr-1 text-stone-400" /> document title:
                    </label>
                    <input
                      type="text"
                      value={item.customName}
                      onChange={(e) => handleNameChange(item.id, e.target.value)}
                      placeholder={item.defaultName}
                      className="w-full bg-white dark:bg-[#1C1510] border border-orange-200/70 dark:border-[#382A20] rounded px-2 py-1 text-xs text-stone-900 dark:text-white font-mono focus:outline-none focus:border-orange-400 transition-colors"
                    />
                  </div>

                  {/* Category Selection */}
                  <div className="flex items-center space-x-1">
                    <span className="text-[10px] text-stone-400">category:</span>
                    {(['Identity', 'Financial', 'Medical', 'Personal', 'Other'] as const).map((cat) => {
                      const isSelected = item.category === cat;
                      let activeColor = 'bg-stone-900 text-white dark:bg-white dark:text-stone-950 font-bold';
                      if (isSelected) {
                        if (cat === 'Identity') activeColor = 'bg-blue-600 text-white font-bold shadow-xs';
                        else if (cat === 'Financial') activeColor = 'bg-emerald-600 text-white font-bold shadow-xs';
                        else if (cat === 'Medical') activeColor = 'bg-teal-600 text-white font-bold shadow-xs';
                        else if (cat === 'Personal') activeColor = 'bg-purple-600 text-white font-bold shadow-xs';
                      }
                      return (
                        <button
                          key={cat}
                          type="button"
                          onClick={() => handleCategoryChange(item.id, cat)}
                          className={`text-[9px] px-1.5 py-0.5 rounded font-mono transition-colors ${
                            isSelected
                              ? activeColor
                              : 'bg-white dark:bg-[#1C1510] text-stone-500 border border-orange-200/60 dark:border-[#382A20] hover:border-orange-400'
                          }`}
                        >
                          {cat.toLowerCase()}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Action Footer */}
          <div className="pt-2 flex items-center justify-between gap-2 border-t border-orange-100 dark:border-[#2D2118]">
            <button
              type="button"
              onClick={() => setStagedFiles([])}
              className="px-2.5 py-1 rounded text-xs text-stone-500 hover:text-stone-900 dark:hover:text-white transition-colors"
            >
              cancel
            </button>

            <div className="flex items-center space-x-1.5">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="px-2.5 py-1 rounded text-xs bg-orange-50/60 dark:bg-[#251A12] border border-orange-200/60 dark:border-[#382A20] text-stone-700 dark:text-stone-300 hover:text-stone-900"
              >
                + add files
              </button>

              <button
                type="button"
                disabled={isUploading}
                onClick={handleFinalSave}
                className="flex items-center space-x-1 px-3 py-1 rounded text-xs font-semibold bg-orange-600 hover:bg-orange-700 text-white dark:bg-orange-500 dark:hover:bg-orange-400 dark:text-stone-950 shadow-sm transition-all disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
              >
                <CheckCircle className="w-3 h-3 text-emerald-200 dark:text-stone-950" />
                <span>{isUploading ? 'uploading to quarantine...' : 'upload & scan'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Security Reassurance */}
      <div className="bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#2D2118] rounded-lg p-2.5 flex items-center space-x-2 text-[10px] text-stone-500 dark:text-stone-400">
        <Shield className="w-3.5 h-3.5 text-stone-500 dark:text-stone-400 flex-shrink-0" />
        <span>End-to-end encrypted • Multi-photo pages bundled into one master record • Zero data leakage</span>
      </div>
    </div>
  );
};

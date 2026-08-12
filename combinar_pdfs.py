#!/usr/bin/env python3
"""
Script para combinar DIGITAL_PUEBLA.pdf + Recibo-puebla.pdf en PUEBLA_PLANTILLA_COMPLETA.pdf
Uso: python combinar_pdfs.py
"""

import fitz
import os

def combinar_pdfs_puebla():
    """
    Combina los dos PDFs en uno solo con ambas páginas
    """
    
    pdf_permiso = "DIGITAL_PUEBLA.pdf"
    pdf_recibo  = "Recibo-puebla.pdf"
    pdf_salida  = "PUEBLA_PLANTILLA_COMPLETA.pdf"
    
    # Validar que existan los archivos
    if not os.path.exists(pdf_permiso):
        print(f"❌ No encontrado: {pdf_permiso}")
        print("   Coloca el PDF del permiso en la carpeta actual")
        return False
    
    if not os.path.exists(pdf_recibo):
        print(f"❌ No encontrado: {pdf_recibo}")
        print("   Coloca el PDF del recibo en la carpeta actual")
        return False
    
    print(f"📖 Abriendo {pdf_permiso}...")
    doc_permiso = fitz.open(pdf_permiso)
    
    print(f"📖 Abriendo {pdf_recibo}...")
    doc_recibo = fitz.open(pdf_recibo)
    
    # Crear documento nuevo
    print(f"\n📝 Combinando PDFs...")
    doc_final = fitz.open()
    
    # Agregar primera página (permiso)
    print(f"   ✓ Página 1: Permiso")
    doc_final.insert_pdf(doc_permiso)
    
    # Agregar segunda página (recibo)
    print(f"   ✓ Página 2: Recibo")
    doc_final.insert_pdf(doc_recibo)
    
    # Guardar archivo combinado
    doc_final.save(pdf_salida)
    doc_final.close()
    doc_permiso.close()
    doc_recibo.close()
    
    print(f"\n✅ Archivo creado: {pdf_salida}")
    print(f"\n📋 Información:")
    print(f"   • Total de páginas: 2")
    print(f"   • Página 1: Permiso Provisional (rojo)")
    print(f"   • Página 2: Recibo (negro)")
    print(f"\n🚀 Ahora puedes:")
    print(f"   1. Subir {pdf_salida} a tu repo GitHub")
    print(f"   2. Render se actualizará automático")
    print(f"   3. El bot generará PDFs con este archivo")
    
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("  COMBINADOR DE PDFs - PUEBLA LICITACIÓN")
    print("=" * 60)
    print()
    
    if combinar_pdfs_puebla():
        print("\n" + "=" * 60)
        print("  ✅ ÉXITO - Listo para Render")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("  ❌ ERROR - Verifica los archivos")
        print("=" * 60)

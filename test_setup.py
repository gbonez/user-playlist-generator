#!/usr/bin/env python3
"""
Test script to verify the music discovery web app setup
"""

import os
import sys
from importlib.util import spec_from_file_location, module_from_spec

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    
    try:
        import flask
        print("✅ Flask imported successfully")
    except ImportError as e:
        print(f"❌ Flask import failed: {e}")
        return False
        
    try:
        import spotipy
        print("✅ Spotipy imported successfully")
    except ImportError as e:
        print(f"❌ Spotipy import failed: {e}")
        return False
        
    try:
        import requests
        print("✅ Requests imported successfully")
    except ImportError as e:
        print(f"❌ Requests import failed: {e}")
        return False
        
    try:
        import selenium
        print("✅ Selenium imported successfully")
    except ImportError as e:
        print(f"❌ Selenium import failed: {e}")
        return False
        
    try:
        import bs4
        print("✅ BeautifulSoup4 imported successfully")
    except ImportError as e:
        print(f"❌ BeautifulSoup4 import failed: {e}")
        return False
        
    return True

def test_lite_script():
    """Test that the lite script can be imported"""
    print("\nTesting lite script...")
    
    try:
        # Try to import the lite script
        spec = spec_from_file_location("lite_script", "lite_script.py")
        if spec is None:
            print("❌ lite_script.py not found")
            return False
            
        lite_script = module_from_spec(spec)
        spec.loader.exec_module(lite_script)
        print("✅ lite_script.py imported successfully")
        
        # Check that key functions exist
        required_functions = [
            'run_lite_script',
            'safe_spotify_call', 
            'validate_track_lite',
            'select_track_for_artist_lite'
        ]
        
        for func_name in required_functions:
            if hasattr(lite_script, func_name):
                print(f"✅ Function {func_name} found")
            else:
                print(f"❌ Function {func_name} missing")
                return False
                
        return True
        
    except Exception as e:
        print(f"❌ lite_script.py import failed: {e}")
        return False

def test_flask_app():
    """Test that the Flask app can be imported"""
    print("\nTesting Flask app...")
    
    try:
        spec = spec_from_file_location("app", "app.py")
        if spec is None:
            print("❌ app.py not found")
            return False
            
        app_module = module_from_spec(spec)
        spec.loader.exec_module(app_module)
        print("✅ app.py imported successfully")
        
        # Check that the Flask app exists
        if hasattr(app_module, 'app'):
            print("✅ Flask app instance found")
        else:
            print("❌ Flask app instance missing")
            return False
            
        return True
        
    except Exception as e:
        print(f"❌ app.py import failed: {e}")
        return False

def test_templates():
    """Test that template files exist"""
    print("\nTesting templates...")
    
    template_files = [
        'templates/base.html',
        'templates/login.html', 
        'templates/dashboard.html',
        'templates/error.html'
    ]
    
    all_found = True
    for template in template_files:
        if os.path.exists(template):
            print(f"✅ {template} found")
        else:
            print(f"❌ {template} missing")
            all_found = False
            
    return all_found

def check_environment():
    """Check environment setup"""
    print("\nChecking environment...")
    
    required_vars = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET']
    optional_vars = ['LASTFM_API_KEY', 'BASE_URL', 'FLASK_SECRET_KEY']
    
    has_required = True
    for var in required_vars:
        if os.environ.get(var):
            print(f"✅ {var} is set")
        else:
            print(f"⚠️  {var} is not set (required for full functionality)")
            has_required = False
            
    for var in optional_vars:
        if os.environ.get(var):
            print(f"✅ {var} is set")
        else:
            print(f"ℹ️  {var} is not set (optional)")
            
    if not has_required:
        print("\n⚠️  Warning: Missing required environment variables.")
        print("   Copy .env.example to .env and fill in your values.")
        
    return has_required

def main():
    """Run all tests"""
    print("🎵 Music Discovery Web App - Setup Test\n")
    
    tests = [
        ("Import Test", test_imports),
        ("Lite Script Test", test_lite_script),
        ("Flask App Test", test_flask_app),
        ("Templates Test", test_templates),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Running {test_name}")
        print('='*50)
        
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    # Environment check (non-blocking)
    print(f"\n{'='*50}")
    print("Environment Check")
    print('='*50)
    env_ok = check_environment()
    
    # Summary
    print(f"\n{'='*50}")
    print("SUMMARY")
    print('='*50)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\nTests passed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 All tests passed! Your setup looks good.")
        if env_ok:
            print("   You can run: python app.py")
        else:
            print("   Set up your .env file and then run: python app.py")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the errors above.")
        
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
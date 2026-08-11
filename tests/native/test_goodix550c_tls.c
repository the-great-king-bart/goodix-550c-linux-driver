/* Offline paired-memory-BIO test for the Goodix TLS wrapper. */
#include "goodix53x5-tls.h"

#include <gio/gio.h>
#include <openssl/ssl.h>
#include <stdio.h>
#include <string.h>

#define EXPECTED_CIPHER "PSK-AES128-CBC-SHA256"
#define EXPECTED_IDENTITY "Client_identity"

static const guint8 synthetic_psk[32] = {
  0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
  0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
  0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
  0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f,
};

static const gchar *active_identity;

static unsigned int
client_psk_cb (SSL          *ssl,
               const char   *hint,
               char         *identity,
               unsigned int  max_identity_len,
               unsigned char *psk,
               unsigned int  max_psk_len)
{
  gsize identity_len = strlen (active_identity) + 1;

  (void) ssl;
  (void) hint;
  if (identity_len > max_identity_len || sizeof (synthetic_psk) > max_psk_len)
    return 0;

  memcpy (identity, active_identity, identity_len);
  memcpy (psk, synthetic_psk, sizeof (synthetic_psk));
  return sizeof (synthetic_psk);
}

static GByteArray *
drain_bio (BIO *bio)
{
  g_autoptr(GByteArray) bytes = g_byte_array_new ();
  guint8 chunk[4096];
  int count;

  while ((count = BIO_read (bio, chunk, sizeof (chunk))) > 0)
    g_byte_array_append (bytes, chunk, (guint) count);

  return g_steal_pointer (&bytes);
}

static gboolean
run_handshake (const gchar *identity,
               gboolean     expect_success)
{
  g_autoptr(GError) error = NULL;
  g_autoptr(GByteArray) client_out = NULL;
  g_autofree guint8 *server_out = NULL;
  g_autofree guint8 *plain = NULL;
  SSL_CTX *client_ctx = NULL;
  SSL *client = NULL;
  BIO *client_rbio = NULL;
  BIO *client_wbio = NULL;
  GoodixTls *server = NULL;
  gboolean server_done = FALSE;
  gboolean server_rejected = FALSE;
  gboolean success = FALSE;

  active_identity = identity;
  client_ctx = SSL_CTX_new (TLS_client_method ());
  if (client_ctx == NULL)
    goto out;
  SSL_CTX_set_min_proto_version (client_ctx, TLS1_2_VERSION);
  SSL_CTX_set_max_proto_version (client_ctx, TLS1_2_VERSION);
  SSL_CTX_set_security_level (client_ctx, 0);
  if (SSL_CTX_set_cipher_list (client_ctx, EXPECTED_CIPHER) != 1)
    goto out;
  SSL_CTX_set_psk_client_callback (client_ctx, client_psk_cb);

  client = SSL_new (client_ctx);
  client_rbio = BIO_new (BIO_s_mem ());
  client_wbio = BIO_new (BIO_s_mem ());
  server = goodix_tls_new (synthetic_psk, sizeof (synthetic_psk));
  if (client == NULL || client_rbio == NULL || client_wbio == NULL || server == NULL)
    goto out;

  SSL_set_bio (client, client_rbio, client_wbio);
  client_rbio = NULL;
  client_wbio = NULL;
  SSL_set_connect_state (client);

  for (guint step = 0; step < 32; step++)
    {
      int connect_result = SSL_connect (client);
      int connect_error = connect_result == 1 ? SSL_ERROR_NONE
                                               : SSL_get_error (client, connect_result);
      gsize server_out_len = 0;

      if (connect_result != 1 && connect_error != SSL_ERROR_WANT_READ &&
          connect_error != SSL_ERROR_WANT_WRITE)
        break;

      g_clear_pointer (&client_out, g_byte_array_unref);
      client_out = drain_bio (SSL_get_wbio (client));
      g_clear_pointer (&server_out, g_free);
      if (!goodix_tls_handshake_pump (server, client_out->data, client_out->len,
                                      &server_out, &server_out_len,
                                      &server_done, &error))
        {
          server_rejected = TRUE;
          break;
        }

      if (server_out_len > 0 &&
          BIO_write (SSL_get_rbio (client), server_out, (int) server_out_len) <= 0)
        break;

      if (SSL_is_init_finished (client) && server_done)
        {
          success = TRUE;
          break;
        }
    }

  if (!expect_success)
    {
      success = server_rejected && !success;
      goto out;
    }
  if (!success || !goodix_tls_is_established (server) ||
      g_strcmp0 (SSL_get_cipher_name (client), EXPECTED_CIPHER) != 0)
    {
      success = FALSE;
      goto out;
    }

  static const guint8 message[] = "offline-goodix-tls";
  if (SSL_write (client, message, sizeof (message)) != (int) sizeof (message))
    {
      success = FALSE;
      goto out;
    }

  g_clear_pointer (&client_out, g_byte_array_unref);
  client_out = drain_bio (SSL_get_wbio (client));
  gsize plain_len = 0;
  if (!goodix_tls_decrypt (server, client_out->data, client_out->len,
                           &plain, &plain_len, &error) ||
      plain_len != sizeof (message) ||
      memcmp (plain, message, sizeof (message)) != 0)
    success = FALSE;

out:
  goodix_tls_free (server);
  SSL_free (client);
  BIO_free (client_rbio);
  BIO_free (client_wbio);
  SSL_CTX_free (client_ctx);
  return success;
}

int
main (void)
{
  if (!run_handshake (EXPECTED_IDENTITY, TRUE))
    {
      fputs ("Expected TLS handshake/decrypt failed.\n", stderr);
      return 1;
    }
  if (!run_handshake ("Unexpected_identity", FALSE))
    {
      fputs ("Unexpected TLS identity was accepted.\n", stderr);
      return 1;
    }

  puts ("Goodix TLS offline policy test passed.");
  return 0;
}
